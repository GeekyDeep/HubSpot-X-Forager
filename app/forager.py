"""Forager.ai API client for company and people enrichment."""
import logging
import os
import httpx
from typing import Optional

log = logging.getLogger(__name__)

BASE_URL = "https://api-v2.forager.ai/api"


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-API-KEY": os.environ["FORAGER_API_KEY"],
    }


def _account_id() -> str:
    return os.environ["FORAGER_ACCOUNT_ID"]


def search_organization(domain: str = None, name: str = None, linkedin_id: str = None) -> Optional[dict]:
    """Search for an organization by domain, name, or LinkedIn identifier. Returns the best match."""
    payload = {"page": 1}
    if linkedin_id:
        payload["linkedin_identifiers"] = [linkedin_id]
    elif domain:
        payload["domains"] = [domain]
    if name and not linkedin_id:
        payload["name"] = name

    url = f"{BASE_URL}/{_account_id()}/datastorage/organization_search/"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        data = resp.json()

    log.info("Forager org search payload=%s → keys=%s total=%s", payload, list(data.keys()), data.get("total_search_results"))
    results = data.get("search_results") or data.get("results") or data.get("data") or []
    return results[0] if results else None


def search_people_at_org(
    org_id: int,
    job_title_filter: Optional[str] = None,
    limit: int = 5,
) -> list[dict]:
    """Find people currently employed at an organization, optionally filtered by job title."""
    payload = {
        "page": 1,
        "role_is_current": True,
        "organization_ids": [org_id],
    }
    if job_title_filter:
        payload["role_title"] = job_title_filter

    url = f"{BASE_URL}/{_account_id()}/datastorage/person_role_search/"
    people = []
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        data = resp.json()

    results = data.get("search_results") or data.get("results") or data.get("data") or []
    for item in results[:limit]:
        person = item.get("person") or item
        people.append(person)
    return people


def get_work_email(person_id: int = None, linkedin_id: str = None) -> Optional[str]:
    """Look up a person's work email address."""
    payload = {}
    if person_id:
        payload["person_id"] = person_id
    if linkedin_id:
        payload["linkedin_public_identifier"] = linkedin_id

    url = f"{BASE_URL}/{_account_id()}/datastorage/person_contacts_lookup/work_emails/"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=_headers())
        if resp.status_code == 200:
            data = resp.json()
            emails = data.get("work_emails") or data.get("emails") or []
            if isinstance(emails, list) and emails:
                return emails[0]
            if isinstance(data.get("email"), str):
                return data["email"]
    return None


def get_personal_email(person_id: int = None, linkedin_id: str = None) -> Optional[str]:
    """Look up a person's personal email address."""
    payload = {}
    if person_id:
        payload["person_id"] = person_id
    if linkedin_id:
        payload["linkedin_public_identifier"] = linkedin_id

    url = f"{BASE_URL}/{_account_id()}/datastorage/person_contacts_lookup/personal_emails/"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=_headers())
        if resp.status_code == 200:
            data = resp.json()
            emails = data.get("personal_emails") or data.get("emails") or []
            if isinstance(emails, list) and emails:
                return emails[0]
            if isinstance(data.get("email"), str):
                return data["email"]
    return None


def get_phone(person_id: int = None, linkedin_id: str = None) -> Optional[str]:
    """Look up a person's phone number."""
    payload = {}
    if person_id:
        payload["person_id"] = person_id
    if linkedin_id:
        payload["linkedin_public_identifier"] = linkedin_id

    url = f"{BASE_URL}/{_account_id()}/datastorage/person_contacts_lookup/phone_numbers/"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=_headers())
        if resp.status_code == 200:
            data = resp.json()
            phone = data.get("phone_number") or data.get("phone")
            return phone
    return None


def enrich_company(domain: str = None, name: str = None, linkedin_id: str = None) -> Optional[dict]:
    """
    Return a fully enriched company dict with all available fields.
    Credits used: 1 (organization_search).
    """
    org = search_organization(domain=domain, name=name, linkedin_id=linkedin_id)
    if not org:
        return None

    return {
        "forager_id": org.get("id"),
        "name": org.get("name"),
        "domain": org.get("domain") or org.get("website"),
        "linkedin_url": org.get("linkedin_url") or _build_linkedin_url(org.get("linkedin_public_identifier")),
        "description": org.get("description"),
        "industry": org.get("industry"),
        "headcount": org.get("employees") or org.get("employee_count"),
        "revenue": org.get("revenue"),
        "founded_year": org.get("founded_year") or org.get("founded"),
        "city": _nested(org, "location", "city") or org.get("city"),
        "state": _nested(org, "location", "state") or org.get("state"),
        "country": _nested(org, "location", "country") or org.get("country"),
        "phone": org.get("phone"),
        "raw": org,
    }


def enrich_person(person: dict, fetch_email: bool = True, fetch_phone: bool = True) -> dict:
    """
    Enrich a person record with contact data.
    Credits used: up to 5 (work email) + 15 (phone) = 20 per person.
    """
    person_id = person.get("id")
    linkedin_id = person.get("linkedin_public_identifier") or person.get("linkedin_id")

    location = person.get("location") or {}
    current_role = _get_current_role(person)

    enriched = {
        "forager_id": person_id,
        "first_name": person.get("first_name"),
        "last_name": person.get("last_name"),
        "full_name": person.get("full_name") or person.get("name"),
        "linkedin_url": person.get("linkedin_url") or _build_linkedin_url(linkedin_id),
        "job_title": current_role.get("title") if current_role else person.get("headline"),
        "company_name": _nested(current_role, "organization", "name") if current_role else None,
        "company_domain": _nested(current_role, "organization", "domain") if current_role else None,
        "company_linkedin_url": _nested(current_role, "organization", "linkedin_url") if current_role else None,
        "city": location.get("city") or person.get("city"),
        "state": location.get("state") or person.get("state"),
        "country": location.get("country") or person.get("country"),
        "description": person.get("description") or person.get("summary"),
        "about": person.get("about"),
        "email": None,
        "phone": None,
        "raw": person,
    }

    if fetch_email and (person_id or linkedin_id):
        enriched["email"] = get_work_email(person_id=person_id, linkedin_id=linkedin_id)

    if fetch_phone and (person_id or linkedin_id):
        enriched["phone"] = get_phone(person_id=person_id, linkedin_id=linkedin_id)

    return enriched


# ── helpers ──────────────────────────────────────────────────────────────────

def _nested(d: dict, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, {})
    return d if d else default


def _build_linkedin_url(identifier: str) -> Optional[str]:
    if not identifier:
        return None
    if identifier.startswith("http"):
        return identifier
    return f"https://www.linkedin.com/in/{identifier}"


def _get_current_role(person: dict) -> Optional[dict]:
    roles = person.get("roles") or person.get("experience") or []
    for role in roles:
        if role.get("is_current") or role.get("end_date") is None:
            return role
    return roles[0] if roles else None
