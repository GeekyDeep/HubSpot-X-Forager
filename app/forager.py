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
    """Search for an organization by domain or name. Returns the best match.
    linkedin_id is used for post-hoc scoring only (Forager ignores it as a search filter).
    """
    payload = {"page": 1}
    if domain:
        payload["domains"] = [domain]
    if name:
        payload["name"] = name

    url = f"{BASE_URL}/{_account_id()}/datastorage/organization_search/"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        data = resp.json()

    log.info("Forager org search payload=%s → total=%s", payload, data.get("total_search_results"))
    results = data.get("search_results") or data.get("results") or data.get("data") or []
    return _best_org_match(results, domain=domain, name=name, linkedin_id=linkedin_id)


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
            return _extract_first_contact(data)
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
            return _extract_first_contact(data)
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
            return _extract_first_contact(data)
    return None


def enrich_company(domain: str = None, name: str = None, linkedin_id: str = None) -> Optional[dict]:
    """
    Return a fully enriched company dict with all available fields.
    Credits used: 1 (organization_search).
    """
    org = search_organization(domain=domain, name=name, linkedin_id=linkedin_id)
    if not org:
        return None

    li_info = org.get("linkedin_info") or {}
    location = org.get("location") or {}
    osm = location.get("osm_locations") or []
    city = next((o["name"].split(",")[0] for o in osm if o.get("place_type") == "city"), None)
    state = next((o["name"] for o in osm if o.get("place_type") == "state"), None)
    country = next((o["name"] for o in osm if o.get("place_type") == "country"), None)
    finance = org.get("finance_info") or {}
    industry = (li_info.get("industry") or {}).get("name") or org.get("industry")

    return {
        "forager_id": org.get("id"),
        "name": org.get("name"),
        "domain": org.get("domain"),
        "website": org.get("website"),
        "linkedin_url": li_info.get("public_profile_url"),
        "description": org.get("description"),
        "industry": industry,
        "headcount": org.get("employees_amount") or org.get("employees"),
        "headcount_range": org.get("employees_range"),
        "revenue": finance.get("revenue"),
        "founded_year": (org.get("founded_date") or "")[:4] or None,
        "operating_status": org.get("operating_status"),
        "city": city,
        "state": state,
        "country": country,
        "phone": org.get("phone"),
        "keywords": [k.get("name") for k in (org.get("keywords") or [])],
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


def _extract_first_contact(data) -> Optional[str]:
    """Handle both list and dict shapes from Forager contact lookup endpoints."""
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        for key in ("work_emails", "personal_emails", "phone_numbers", "emails", "phones"):
            val = data.get(key)
            if isinstance(val, list) and val:
                return val[0]
        for key in ("work_email", "personal_email", "phone_number", "email", "phone"):
            if data.get(key):
                return data[key]
    return None


def _best_org_match(results: list, domain: str = None, name: str = None, linkedin_id: str = None) -> Optional[dict]:
    """Score and return the best matching org from Forager search results."""
    if not results:
        return None

    def score(org: dict) -> int:
        s = 0
        org_domain = (org.get("domain") or "").lower()
        org_name = (org.get("name") or "").lower()
        li_identifier = (org.get("linkedin_info") or {}).get("public_identifier", "").lower()

        if linkedin_id and li_identifier == linkedin_id.lower():
            s += 100
        if domain and org_domain == domain.lower():
            s += 80
        if name and org_name == name.lower():
            s += 60
        # Prefer orgs with more employees (larger, more likely to be the main entity)
        employees = org.get("employees_amount") or 0
        s += min(employees // 1000, 20)
        return s

    return max(results, key=score)
