"""
Forager × HubSpot Enrichment Automation
FastAPI service that:
  - Receives HubSpot webhooks and auto-enriches companies / contacts via Forager
  - Exposes manual trigger endpoints for on-demand enrichment
  - Provides a /demo/openai endpoint for the evaluation demo
"""
import hashlib
import hmac
import httpx
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel

from app import forager
from app import hubspot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Forager × HubSpot Enrichment", version="1.0.0")


@app.exception_handler(httpx.ConnectError)
async def connect_error_handler(request: Request, exc: httpx.ConnectError):
    log.error("Network connection failed: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "Cannot reach external API — check your internet connection and try again."},
    )


@app.exception_handler(httpx.HTTPStatusError)
async def http_status_error_handler(request: Request, exc: httpx.HTTPStatusError):
    log.error("Upstream HTTP error: %s %s — %s", exc.response.status_code, exc.request.url, exc.response.text[:300])
    return JSONResponse(
        status_code=502,
        content={"detail": f"Upstream API error {exc.response.status_code}: {exc.response.text[:300]}"},
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    log.exception("Unhandled exception in %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )


# ── Request / Response models ──────────────────────────────────────────────────

class EnrichCompanyRequest(BaseModel):
    domain: Optional[str] = None
    name: Optional[str] = None
    linkedin_url: Optional[str] = None
    push_to_hubspot: bool = True


class EnrichPeopleRequest(BaseModel):
    company_domain: Optional[str] = None
    company_name: Optional[str] = None
    job_title_filter: Optional[str] = None
    limit: int = 5
    push_to_hubspot: bool = True
    fetch_email: bool = True
    fetch_phone: bool = True


class SearchCompanyRequest(BaseModel):
    domain: Optional[str] = None
    name: Optional[str] = None


class EnrichEmailPhoneRequest(BaseModel):
    contact_ids: list[str]
    fetch_email: bool = True
    fetch_phone: bool = True


class DiscoverPeopleRequest(BaseModel):
    org_id: int
    page: int = 0
    domain: Optional[str] = None
    job_title_filter: Optional[str] = None


class PushContactsRequest(BaseModel):
    contacts: list[dict]
    company_hubspot_id: Optional[str] = None


# ── UI ────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def ui():
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/debug/forager")
def debug_forager(domain: str = None, name: str = None, linkedin_id: str = None):
    """Raw Forager org search — returns full API response for debugging."""
    import httpx as _httpx, os as _os
    payload = {"page": 0}
    if linkedin_id:
        payload["linkedin_public_identifiers"] = [linkedin_id]
    elif domain:
        payload["domains"] = [domain]
    url = f"https://api-v2.forager.ai/api/{_os.environ['FORAGER_ACCOUNT_ID']}/datastorage/organization_search/"
    headers = {"Content-Type": "application/json", "X-API-KEY": _os.environ["FORAGER_API_KEY"]}
    resp = _httpx.post(url, json=payload, headers=headers, timeout=30)
    return {"status": resp.status_code, "body": resp.json() if resp.status_code == 200 else resp.text}


@app.get("/debug/people")
def debug_people(org_id: int, job_title: str = None):
    """Raw Forager person role search — returns full API response for debugging."""
    import httpx as _httpx, os as _os
    payload = {"page": 0, "role_is_current": True, "organizations": [org_id]}
    if job_title:
        payload["role_title"] = job_title
    url = f"https://api-v2.forager.ai/api/{_os.environ['FORAGER_ACCOUNT_ID']}/datastorage/person_role_search/"
    headers = {"Content-Type": "application/json", "X-API-KEY": _os.environ["FORAGER_API_KEY"]}
    resp = _httpx.post(url, json=payload, headers=headers, timeout=30)
    return {"status": resp.status_code, "body": resp.json() if resp.status_code == 200 else resp.text}


# ── Manual enrichment endpoints ────────────────────────────────────────────────

@app.post("/enrich/company")
def enrich_company(req: EnrichCompanyRequest):
    """
    Enrich a single company by domain or name and (optionally) push to HubSpot.
    """
    if not req.domain and not req.name and not req.linkedin_url:
        raise HTTPException(400, "Provide at least one of: domain, name, linkedin_url")

    linkedin_id = forager.extract_linkedin_company_slug(req.linkedin_url) if req.linkedin_url else None
    log.info("Enriching company domain=%s name=%s linkedin_id=%s", req.domain, req.name, linkedin_id)
    enriched = forager.enrich_company(domain=req.domain, name=req.name, linkedin_id=linkedin_id)
    if not enriched:
        raise HTTPException(404, "Company not found in Forager")

    result = {"forager": enriched}
    if req.push_to_hubspot:
        hs_result = hubspot.upsert_company(enriched)
        result["hubspot"] = hs_result

    return result


@app.post("/enrich/people")
def enrich_people(req: EnrichPeopleRequest):
    """
    Find people currently employed at a company, enrich them, and push to HubSpot.
    Supports optional job_title_filter to control which roles are surfaced.
    """
    if not req.company_domain and not req.company_name:
        raise HTTPException(400, "Provide at least one of: company_domain, company_name")

    log.info("Searching Forager for company domain=%s name=%s", req.company_domain, req.company_name)
    org = forager.search_organization(domain=req.company_domain, name=req.company_name)
    if not org:
        raise HTTPException(404, "Company not found in Forager")

    org_id = org.get("id")
    log.info("Found org id=%s name=%s", org_id, org.get("name"))

    people_raw = forager.search_people_at_org(
        org_id=org_id,
        domain=req.company_domain,
        job_title_filter=req.job_title_filter,
        limit=req.limit,
    )
    log.info("Found %d people", len(people_raw))

    # Upsert company in HubSpot first so we have the company_id for association
    company_hs_id = None
    if req.push_to_hubspot:
        enriched_org = forager.enrich_company(name=org.get("name"), domain=org.get("domain"))
        if enriched_org:
            hs_company = hubspot.upsert_company(enriched_org)
            company_hs_id = hs_company.get("id")

    results = []
    for person in people_raw:
        enriched = forager.enrich_person(
            person, fetch_email=req.fetch_email, fetch_phone=req.fetch_phone
        )
        entry = {"forager": enriched}
        if req.push_to_hubspot:
            hs_contact = hubspot.upsert_contact(enriched, company_id=company_hs_id)
            entry["hubspot"] = hs_contact
        results.append(entry)

    return {"company": org.get("name"), "people_count": len(results), "results": results}


# ── Company search (via HubSpot) ──────────────────────────────────────────────

@app.post("/search/company")
def search_company(req: SearchCompanyRequest):
    if not req.domain and not req.name:
        raise HTTPException(400, "Provide at least one of: domain, name")
    result = hubspot.find_company_for_discover(domain=req.domain, name=req.name)
    if not result:
        raise HTTPException(404, "Company not found in HubSpot. Add it there first — the webhook will enrich it automatically.")
    if not result.get("forager_company_id"):
        raise HTTPException(400, f"'{result['name']}' is in HubSpot but hasn't been enriched by Forager yet. Wait a moment and try again.")
    return {
        "forager_id": int(result["forager_company_id"]),
        "hubspot_id": result["hubspot_id"],
        "name": result["name"],
        "domain": result["domain"],
    }


# ── HubSpot company + contact lookups (for Email/Phone tab) ───────────────────

@app.get("/hubspot/companies")
def hubspot_companies(name: str):
    return {"companies": hubspot.search_companies_by_name(name)}


@app.get("/hubspot/companies/{company_id}/contacts")
def company_contacts(company_id: str):
    return {"contacts": hubspot.get_company_contacts(company_id)}


@app.post("/enrich/contacts/email-phone")
def enrich_email_phone(req: EnrichEmailPhoneRequest):
    results = []
    for cid in req.contact_ids:
        props = hubspot._get_contact_props(cid, ["forager_person_id"])
        person_id_str = props.get("forager_person_id")
        if not person_id_str:
            results.append({"hubspot_id": cid, "error": "no forager_person_id"})
            continue
        try:
            person_id = int(person_id_str)
        except (ValueError, TypeError):
            results.append({"hubspot_id": cid, "error": f"invalid forager_person_id: {person_id_str!r}"})
            continue
        email = forager.get_work_email(person_id=person_id) if req.fetch_email else None
        phone = forager.get_phone(person_id=person_id) if req.fetch_phone else None
        update = {k: v for k, v in {"email": email, "phone": phone}.items() if v}
        if update:
            hubspot._update_contact(cid, update)
        results.append({"hubspot_id": cid, "email": email, "phone": phone})
    return {"results": results}


# ── People discovery: page-by-page with filter breakdown ─────────────────────

@app.post("/discover/people")
def discover_people(req: DiscoverPeopleRequest):
    """
    Fetch one page of people at an org with a legitimacy filter breakdown.
    Returns valid profiles AND filtered-out profiles (with reasons) so the caller
    can review rejects and decide whether to fetch the next page.

    Pagination is caller-driven: increment `page` to get the next batch.
    `total_pages` in the response tells you how many pages exist in total.
    """
    result = forager.search_people_page(
        org_id=req.org_id,
        page=req.page,
        domain=req.domain,
        job_title_filter=req.job_title_filter,
    )
    return result


@app.post("/push/contacts")
def push_contacts(req: PushContactsRequest):
    """
    Push valid_profiles from /discover/people to HubSpot contacts.
    No email or phone lookup — zero extra Forager credits.
    """
    results = []
    for person in req.contacts:
        enriched = forager.enrich_person(person, fetch_email=False, fetch_phone=False)
        hs = hubspot.upsert_contact(enriched, company_id=req.company_hubspot_id)
        results.append({
            "name": enriched.get("full_name"),
            "linkedin_url": enriched.get("linkedin_url"),
            "job_title": enriched.get("job_title"),
            "hubspot_id": hs.get("id"),
            "action": hs.get("action"),
        })
    log.info("Pushed %d contacts to HubSpot", len(results))
    return {"pushed": len(results), "results": results}


# ── Demo endpoint: enrich OpenAI + 5 people ───────────────────────────────────

@app.post("/demo/openai")
def demo_openai(
    background_tasks: BackgroundTasks,
    job_title_filter: Optional[str] = None,
    limit: int = 5,
    fetch_email: bool = True,
    fetch_phone: bool = False,
):
    """
    Demo endpoint: enriches the OpenAI company plus up to `limit` employees.
    Pushes everything to HubSpot. Optionally filter by job title.
    Runs synchronously so you can watch the full trace in logs / response.
    """
    log.info("=== DEMO: Enriching OpenAI + %d people ===", limit)

    # 1. Enrich company via LinkedIn slug — exact match, bypasses domain pollution.
    enriched_org = forager.enrich_company(linkedin_id="openai", domain="openai.com")
    if not enriched_org:
        raise HTTPException(404, "OpenAI not found in Forager")
    log.info("Company enriched: %s", enriched_org.get("name"))

    hs_company = hubspot.upsert_company(enriched_org)
    company_hs_id = hs_company.get("id")
    log.info("HubSpot company id=%s action=%s", company_hs_id, hs_company.get("action"))

    # 2. Find people at OpenAI
    people_raw = forager.search_people_at_org(
        org_id=enriched_org["forager_id"],
        domain="openai.com",
        job_title_filter=job_title_filter,
        limit=limit,
    )
    log.info("Found %d people to enrich", len(people_raw))

    # 3. Enrich each person and push to HubSpot
    people_results = []
    for person in people_raw:
        enriched = forager.enrich_person(person, fetch_email=fetch_email, fetch_phone=fetch_phone)
        hs_contact = hubspot.upsert_contact(enriched, company_id=company_hs_id)
        log.info(
            "Contact: %s %s | email=%s | phone=%s | hs_action=%s",
            enriched.get("first_name"),
            enriched.get("last_name"),
            enriched.get("email"),
            enriched.get("phone"),
            hs_contact.get("action"),
        )
        people_results.append({
            "name": f"{enriched.get('first_name')} {enriched.get('last_name')}",
            "title": enriched.get("job_title"),
            "email": enriched.get("email"),
            "phone": enriched.get("phone"),
            "city": enriched.get("city"),
            "state": enriched.get("state"),
            "country": enriched.get("country"),
            "linkedin": enriched.get("linkedin_url"),
            "hubspot_id": hs_contact.get("id"),
            "hubspot_action": hs_contact.get("action"),
        })

    return {
        "company": {
            "name": enriched_org.get("name"),
            "domain": enriched_org.get("domain"),
            "description": enriched_org.get("description"),
            "industry": enriched_org.get("industry"),
            "headcount": enriched_org.get("headcount"),
            "revenue": enriched_org.get("revenue"),
            "city": enriched_org.get("city"),
            "state": enriched_org.get("state"),
            "country": enriched_org.get("country"),
            "linkedin_url": enriched_org.get("linkedin_url"),
            "hubspot_id": company_hs_id,
            "hubspot_action": hs_company.get("action"),
        },
        "people": people_results,
    }


# ── HubSpot Webhook receiver ───────────────────────────────────────────────────

@app.post("/webhook/hubspot")
async def hubspot_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receives HubSpot webhook events (contact.creation, company.creation).
    Validates the HMAC signature when HUBSPOT_CLIENT_SECRET is configured,
    then triggers async Forager enrichment.
    """
    raw_body = await request.body()

    # Validate signature if secret is configured
    client_secret = os.environ.get("HUBSPOT_CLIENT_SECRET")
    if client_secret:
        sig_header = request.headers.get("X-HubSpot-Signature-v3", "")
        if not _verify_hubspot_signature(client_secret, sig_header, raw_body):
            raise HTTPException(401, "Invalid webhook signature")

    events = await request.json()
    if not isinstance(events, list):
        events = [events]

    log.info("Received %d HubSpot webhook event(s)", len(events))

    for event in events:
        event_type = event.get("subscriptionType", "")
        object_id = str(event.get("objectId", ""))

        if event_type == "company.creation":
            background_tasks.add_task(_enrich_hubspot_company, object_id)
        elif event_type == "contact.creation":
            background_tasks.add_task(_enrich_hubspot_contact, object_id)
        else:
            log.info("Ignoring event type: %s", event_type)

    return {"received": len(events)}


def _verify_hubspot_signature(secret: str, sig_header: str, body: bytes) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def _enrich_hubspot_company(company_id: str) -> None:
    """Background task: fetch a newly created HubSpot company and enrich it via Forager."""
    try:
        import httpx as _httpx
        url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
        params = {"properties": "name,domain"}
        resp = _httpx.get(url, params=params, headers=hubspot._headers(), timeout=15)
        resp.raise_for_status()
        props = resp.json().get("properties", {})
        domain = props.get("domain")
        name = props.get("name")

        enriched = forager.enrich_company(domain=domain, name=name)
        if enriched:
            hubspot.upsert_company(enriched)
            log.info("Webhook enriched company %s", company_id)

        # Enrich already-associated contacts that don't have a Forager ID yet
        _enrich_associated_contacts(company_id)
    except Exception as exc:
        log.error("Failed to enrich company %s: %s", company_id, exc)


def _enrich_associated_contacts(company_id: str) -> None:
    """Enrich contacts already linked to a company that lack a Forager person ID."""
    try:
        import httpx as _httpx
        url = f"https://api.hubapi.com/crm/v4/objects/companies/{company_id}/associations/contacts"
        resp = _httpx.get(url, headers=hubspot._headers(), timeout=15)
        if not resp.is_success:
            log.warning("Could not get associations for company %s: %s", company_id, resp.text)
            return
        contact_ids = [str(r["toObjectId"]) for r in resp.json().get("results", [])]
        if not contact_ids:
            return
        log.info("Enriching %d associated contact(s) for company %s", len(contact_ids), company_id)
        for cid in contact_ids:
            _enrich_hubspot_contact(cid)
    except Exception as exc:
        log.error("Failed to enrich associated contacts for company %s: %s", company_id, exc)


def _enrich_hubspot_contact(contact_id: str) -> None:
    """
    Background task: enrich a HubSpot contact via Forager person_detail_lookup.
    Requires linkedin_profile_url to be set on the contact. Skips if forager_person_id
    is already populated. Does not fetch email/phone — zero extra credits beyond the
    1-credit detail lookup.
    """
    try:
        import httpx as _httpx
        url = f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}"
        params = {"properties": "linkedin_profile_url,forager_person_id"}
        resp = _httpx.get(url, params=params, headers=hubspot._headers(), timeout=15)
        resp.raise_for_status()
        props = resp.json().get("properties", {})

        if props.get("forager_person_id"):
            log.info("Contact %s already enriched by Forager; skipping", contact_id)
            return

        linkedin_url = props.get("linkedin_profile_url")
        if not linkedin_url:
            log.info("Contact %s has no linkedin_profile_url; skipping Forager enrichment", contact_id)
            return

        # Extract slug from full URL (or use as-is if already a slug)
        linkedin_slug = linkedin_url.rstrip("/").split("/")[-1].split("?")[0]
        person = forager.lookup_person_detail(linkedin_identifier=linkedin_slug)
        if not person:
            log.warning("Contact %s: person_detail_lookup returned nothing for %s", contact_id, linkedin_slug)
            return

        enriched = forager.enrich_person(person, fetch_email=False, fetch_phone=False)
        hubspot.upsert_contact(enriched)
        log.info("Webhook enriched contact %s via LinkedIn slug %s", contact_id, linkedin_slug)
    except Exception as exc:
        log.error("Failed to enrich contact %s: %s", contact_id, exc)
