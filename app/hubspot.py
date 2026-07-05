"""HubSpot CRM API client — creates/updates companies and contacts."""
import os
import httpx
from typing import Optional

BASE_URL = "https://api.hubapi.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['HUBSPOT_ACCESS_TOKEN']}",
        "Content-Type": "application/json",
    }


# ── Companies ─────────────────────────────────────────────────────────────────

def upsert_company(enriched: dict) -> dict:
    """Create or update a HubSpot company from Forager enrichment data."""
    domain = enriched.get("domain")
    existing_id = _find_company_by_domain(domain) if domain else None

    props = _company_props(enriched)
    if existing_id:
        return _update_company(existing_id, props)
    return _create_company(props)


def _company_props(enriched: dict) -> dict:
    return {k: v for k, v in {
        "name": enriched.get("name"),
        "domain": enriched.get("domain"),
        "description": enriched.get("description"),
        "numberofemployees": enriched.get("headcount"),   # HubSpot expects integer
        "city": enriched.get("city"),
        "state": enriched.get("state"),
        "country": enriched.get("country"),
        "phone": enriched.get("phone"),
        "website": enriched.get("website") or enriched.get("domain"),
        "hs_linkedin_company_page": enriched.get("linkedin_url"),  # standard HS property
    }.items() if v is not None}


def _find_company_by_domain(domain: str) -> Optional[str]:
    url = f"{BASE_URL}/crm/v3/objects/companies/search"
    payload = {
        "filterGroups": [{"filters": [{"propertyName": "domain", "operator": "EQ", "value": domain}]}],
        "properties": ["id"],
        "limit": 1,
    }
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json=payload, headers=_headers())
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                return results[0]["id"]
    return None


def _create_company(props: dict) -> dict:
    url = f"{BASE_URL}/crm/v3/objects/companies"
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json={"properties": props}, headers=_headers())
        if not resp.is_success:
            raise ValueError(f"HubSpot company create failed {resp.status_code}: {resp.text}")
        data = resp.json()
        return {"id": data["id"], "action": "created", "properties": data.get("properties", {})}


def _update_company(company_id: str, props: dict) -> dict:
    url = f"{BASE_URL}/crm/v3/objects/companies/{company_id}"
    with httpx.Client(timeout=15) as client:
        resp = client.patch(url, json={"properties": props}, headers=_headers())
        if not resp.is_success:
            raise ValueError(f"HubSpot company update failed {resp.status_code}: {resp.text}")
        data = resp.json()
        return {"id": data["id"], "action": "updated", "properties": data.get("properties", {})}


# ── Contacts ──────────────────────────────────────────────────────────────────

def upsert_contact(enriched: dict, company_id: Optional[str] = None) -> dict:
    """Create or update a HubSpot contact from Forager enrichment data."""
    email = enriched.get("email")
    linkedin = enriched.get("linkedin_url")

    existing_id = None
    if email:
        existing_id = _find_contact_by_email(email)
    if not existing_id and linkedin:
        existing_id = _find_contact_by_linkedin(linkedin)

    props = _contact_props(enriched)
    if existing_id:
        result = _update_contact(existing_id, props)
    else:
        result = _create_contact(props)

    if company_id and result.get("id"):
        _associate_contact_company(result["id"], company_id)

    return result


def _contact_props(enriched: dict) -> dict:
    return {k: v for k, v in {
        "firstname": enriched.get("first_name"),
        "lastname": enriched.get("last_name"),
        "email": enriched.get("email"),
        "phone": enriched.get("phone"),
        "jobtitle": enriched.get("job_title"),
        "company": enriched.get("company_name"),
        "city": enriched.get("city"),
        "state": enriched.get("state"),
        "country": enriched.get("country"),
        "hs_linkedin_handle": _linkedin_handle(enriched.get("linkedin_url")),
        "website": enriched.get("company_domain"),
    }.items() if v is not None}


def _find_contact_by_email(email: str) -> Optional[str]:
    url = f"{BASE_URL}/crm/v3/objects/contacts/search"
    payload = {
        "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
        "properties": ["id"],
        "limit": 1,
    }
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json=payload, headers=_headers())
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                return results[0]["id"]
    return None


def _find_contact_by_linkedin(linkedin_url: str) -> Optional[str]:
    handle = _linkedin_handle(linkedin_url)
    if not handle:
        return None
    url = f"{BASE_URL}/crm/v3/objects/contacts/search"
    payload = {
        "filterGroups": [{"filters": [{"propertyName": "hs_linkedin_handle", "operator": "EQ", "value": handle}]}],
        "properties": ["id"],
        "limit": 1,
    }
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json=payload, headers=_headers())
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                return results[0]["id"]
    return None


def _create_contact(props: dict) -> dict:
    url = f"{BASE_URL}/crm/v3/objects/contacts"
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json={"properties": props}, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
        return {"id": data["id"], "action": "created", "properties": data.get("properties", {})}


def _update_contact(contact_id: str, props: dict) -> dict:
    url = f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}"
    with httpx.Client(timeout=15) as client:
        resp = client.patch(url, json={"properties": props}, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
        return {"id": data["id"], "action": "updated", "properties": data.get("properties", {})}


def _associate_contact_company(contact_id: str, company_id: str) -> None:
    url = f"{BASE_URL}/crm/v4/objects/contacts/{contact_id}/associations/companies/{company_id}"
    body = [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 1}]
    with httpx.Client(timeout=15) as client:
        client.put(url, json=body, headers=_headers())


# ── helpers ───────────────────────────────────────────────────────────────────

def _linkedin_handle(url: str) -> Optional[str]:
    if not url:
        return None
    # Strip trailing slash, then take last path segment
    parts = url.rstrip("/").split("/")
    return parts[-1] if parts else None
