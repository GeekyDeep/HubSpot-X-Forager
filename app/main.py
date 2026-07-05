"""
Forager × HubSpot Enrichment Automation
FastAPI service that:
  - Receives HubSpot webhooks and auto-enriches companies / contacts via Forager
  - Exposes manual trigger endpoints for on-demand enrichment
  - Provides a /demo/openai endpoint for the evaluation demo
"""
import hashlib
import hmac
import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import forager
from app import hubspot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Forager × HubSpot Enrichment", version="1.0.0")


# ── Request / Response models ──────────────────────────────────────────────────

class EnrichCompanyRequest(BaseModel):
    domain: Optional[str] = None
    name: Optional[str] = None
    push_to_hubspot: bool = True


class EnrichPeopleRequest(BaseModel):
    company_domain: Optional[str] = None
    company_name: Optional[str] = None
    job_title_filter: Optional[str] = None
    limit: int = 5
    push_to_hubspot: bool = True
    fetch_email: bool = True
    fetch_phone: bool = True


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/debug/forager")
def debug_forager(domain: str = "openai.com", name: str = None):
    """Raw Forager org search — returns full API response for debugging."""
    import httpx as _httpx, os as _os
    payload = {"page": 1}
    if domain:
        payload["domains"] = [domain]
    if name:
        payload["name"] = name
    url = f"https://api-v2.forager.ai/api/{_os.environ['FORAGER_ACCOUNT_ID']}/datastorage/organization_search/"
    headers = {"Content-Type": "application/json", "Authorization": f"Api-Key {_os.environ['FORAGER_API_KEY']}"}
    resp = _httpx.post(url, json=payload, headers=headers, timeout=30)
    return {"status": resp.status_code, "body": resp.json() if resp.status_code == 200 else resp.text}


# ── Manual enrichment endpoints ────────────────────────────────────────────────

@app.post("/enrich/company")
def enrich_company(req: EnrichCompanyRequest):
    """
    Enrich a single company by domain or name and (optionally) push to HubSpot.
    """
    if not req.domain and not req.name:
        raise HTTPException(400, "Provide at least one of: domain, name")

    log.info("Enriching company domain=%s name=%s", req.domain, req.name)
    enriched = forager.enrich_company(domain=req.domain, name=req.name)
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


# ── Demo endpoint: enrich OpenAI + 5 people ───────────────────────────────────

@app.post("/demo/openai")
def demo_openai(
    background_tasks: BackgroundTasks,
    job_title_filter: Optional[str] = None,
    limit: int = 5,
):
    """
    Demo endpoint: enriches the OpenAI company plus up to `limit` employees.
    Pushes everything to HubSpot. Optionally filter by job title.
    Runs synchronously so you can watch the full trace in logs / response.
    """
    log.info("=== DEMO: Enriching OpenAI + %d people ===", limit)

    # 1. Enrich company
    enriched_org = forager.enrich_company(domain="openai.com", name="OpenAI")
    if not enriched_org:
        raise HTTPException(404, "OpenAI not found in Forager")
    log.info("Company enriched: %s", enriched_org.get("name"))

    hs_company = hubspot.upsert_company(enriched_org)
    company_hs_id = hs_company.get("id")
    log.info("HubSpot company id=%s action=%s", company_hs_id, hs_company.get("action"))

    # 2. Find people at OpenAI
    people_raw = forager.search_people_at_org(
        org_id=enriched_org["forager_id"],
        job_title_filter=job_title_filter,
        limit=limit,
    )
    log.info("Found %d people to enrich", len(people_raw))

    # 3. Enrich each person and push to HubSpot
    people_results = []
    for person in people_raw:
        enriched = forager.enrich_person(person, fetch_email=True, fetch_phone=True)
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
    except Exception as exc:
        log.error("Failed to enrich company %s: %s", company_id, exc)


def _enrich_hubspot_contact(contact_id: str) -> None:
    """Background task: fetch a newly created HubSpot contact and enrich it via Forager."""
    try:
        import httpx as _httpx
        url = f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}"
        params = {"properties": "email,firstname,lastname,hs_linkedin_handle,company"}
        resp = _httpx.get(url, params=params, headers=hubspot._headers(), timeout=15)
        resp.raise_for_status()
        props = resp.json().get("properties", {})

        linkedin_id = props.get("hs_linkedin_handle")
        # We can enrich by searching for the person via LinkedIn if available
        if linkedin_id:
            person_stub = {"linkedin_public_identifier": linkedin_id}
            enriched = forager.enrich_person(person_stub, fetch_email=True, fetch_phone=True)
            if enriched:
                hubspot.upsert_contact(enriched)
                log.info("Webhook enriched contact %s via LinkedIn %s", contact_id, linkedin_id)
        else:
            log.info("Contact %s has no LinkedIn handle; skipping Forager enrichment", contact_id)
    except Exception as exc:
        log.error("Failed to enrich contact %s: %s", contact_id, exc)
