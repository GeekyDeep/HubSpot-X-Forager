"""Forager.ai API client for company and people enrichment."""
import logging
import math
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
    """Search for an organization. Priority: LinkedIn slug > domain.

    linkedin_id must be the bare slug (e.g. "openai"), not a full URL.
    Callers are responsible for extracting the slug via extract_linkedin_company_slug().
    """
    # Priority 1: LinkedIn public identifier — exact match, always returns 1 result
    if linkedin_id:
        results = _org_search_payload({"page": 0, "linkedin_public_identifiers": [linkedin_id]})
        if results:
            return results[0]

    # Priority 2: Domain search
    if domain:
        results = _org_search_payload({"page": 0, "domains": [domain]})
        if not results:
            return None
        if len(results) == 1:
            return results[0]
        # Multiple results — always score to avoid wrong domain matches in Forager's data
        return _best_org_match(results, domain=domain, name=name, linkedin_id=linkedin_id)

    return None


def _org_search_payload(payload: dict) -> list[dict]:
    url = f"{BASE_URL}/{_account_id()}/datastorage/organization_search/"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    log.info("Forager org search payload=%s → total=%s", payload, data.get("total_search_results"))
    return data.get("search_results") or data.get("results") or data.get("data") or []


def search_people_at_org(
    org_id: int,
    domain: str = None,
    job_title_filter: Optional[str] = None,
    limit: int = 5,
) -> list[dict]:
    """Find people currently employed at an organization (page 0 only, backward-compat)."""
    result = search_people_page(org_id=org_id, page=0, domain=domain, job_title_filter=job_title_filter)
    return result["valid_profiles"][:limit]


def search_people_page(
    org_id: int,
    page: int = 0,
    domain: str = None,
    job_title_filter: Optional[str] = None,
    seen_role_descriptions: Optional[set] = None,
) -> dict:
    """
    Fetch one page of person_role_search with legitimacy filtering.
    Returns valid profiles, filtered profiles (with reasons), and pagination info.
    Pass seen_role_descriptions across calls to catch cross-page duplicates.
    """
    if seen_role_descriptions is None:
        seen_role_descriptions = set()

    payload = {"page": page, "role_is_current": True, "organizations": [org_id]}
    if domain:
        payload["organization_domains"] = [domain]
    if job_title_filter:
        payload["role_title"] = _normalize_role_title(job_title_filter)

    url = f"{BASE_URL}/{_account_id()}/datastorage/person_role_search/"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=_headers())
        if not resp.is_success:
            log.error("person_role_search 400: payload=%s body=%s", payload, resp.text)
            resp.raise_for_status()
        data = resp.json()

    total = data.get("total") or data.get("total_search_results") or 0
    total_pages = math.ceil(total / 10) if total else 1
    raw_results = data.get("search_results") or data.get("results") or data.get("data") or []
    log.info("person_role_search org=%s page=%s total=%s total_pages=%s", org_id, page, total, total_pages)

    valid, filtered = [], []
    for item in raw_results:
        person = item.get("person") or {}
        is_legit, reason = _check_legitimacy(item, seen_role_descriptions)
        if is_legit:
            valid.append(_build_person(item))
        else:
            li_info = person.get("linkedin_info") or {}
            filtered.append({
                "name": person.get("full_name"),
                "linkedin_url": li_info.get("public_profile_url"),
                "role_title": item.get("role_title"),
                "reason": reason,
            })

    return {
        "page": page,
        "total": total,
        "total_pages": total_pages,
        "results_on_page": len(raw_results),
        "valid_count": len(valid),
        "filtered_count": len(filtered),
        "valid_profiles": valid,
        "filtered_profiles": filtered,
    }


def _build_person(item: dict) -> dict:
    person = item.get("person") or {}
    org = item.get("organization") or {}
    return {
        **person,
        "roles": [{
            "title": item.get("role_title"),
            "is_current": True,
            "start_date": item.get("start_date"),
            "end_date": None,
            "organization": org,
        }],
    }


def _check_legitimacy(item: dict, seen_role_descriptions: set) -> tuple[bool, str | None]:
    """
    Loose legitimacy filter — three context-free hard blocks.
    Errs strongly toward inclusion: false positives acceptable, false negatives are not.
    """
    person = item.get("person") or {}
    org = item.get("organization") or {}

    # Hard block 1: start date before org was founded (mathematically impossible)
    start_year = (item.get("start_date") or "")[:4]
    founded_year = (org.get("founded_date") or "")[:4]
    if start_year and founded_year and start_year < founded_year:
        return False, f"Start date {start_year} predates {org.get('name', 'company')} founding ({founded_year})"

    # Hard block 2: bio says "previously worked at [org]" while is_current=True
    org_name = (org.get("name") or "").lower()
    bio = (person.get("description") or "").lower()
    if item.get("is_current") and org_name:
        past_markers = [
            f"previously worked at {org_name}",
            f"formerly at {org_name}",
            f"i have previously worked at {org_name}",
            f"used to work at {org_name}",
        ]
        if any(m in bio for m in past_markers):
            return False, "Bio uses past tense ('previously worked at') while role is marked as current"

    # Hard block 3: duplicate role description (copy-paste fake)
    role_desc = (item.get("description") or "").strip()
    if role_desc and len(role_desc) > 60:
        if role_desc in seen_role_descriptions:
            return False, "Role description is an exact duplicate of another result (copy-paste profile)"
        seen_role_descriptions.add(role_desc)

    return True, None


def lookup_person_detail(person_id: int = None, linkedin_identifier: str = None) -> Optional[dict]:
    """
    Fetch full person profile via person_detail_lookup. Costs 1 credit.
    Pass linkedin_identifier as the bare slug (not the full URL).
    Returns a person dict with roles natively included, ready for enrich_person().
    """
    payload = {}
    if person_id:
        payload["person_id"] = person_id
    if linkedin_identifier:
        payload["linkedin_public_identifier"] = linkedin_identifier
    if not payload:
        return None

    url = f"{BASE_URL}/{_account_id()}/datastorage/person_detail_lookup/"
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload, headers=_headers())
        if resp.status_code == 404:
            log.warning("person_detail_lookup not found: person_id=%s linkedin=%s", person_id, linkedin_identifier)
            return None
        resp.raise_for_status()
        return resp.json()


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
        "founded_year": int(yr) if (yr := (org.get("founded_date") or "")[:4]) else None,
        "operating_status": org.get("operating_status"),
        "city": city,
        "state": state,
        "country": country,
        "location_name": location.get("name"),
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
    li_info = person.get("linkedin_info") or {}
    linkedin_id = (
        person.get("linkedin_public_identifier")
        or person.get("linkedin_id")
        or li_info.get("public_identifier")
    )
    linkedin_url = (
        person.get("linkedin_url")
        or li_info.get("public_profile_url")
        or _build_linkedin_url(linkedin_id)
    )

    # Location: Forager persons use osm_locations (same as orgs), not flat city/state/country
    location = person.get("location") or {}
    osm = location.get("osm_locations") or []
    city = next((o["name"].split(",")[0] for o in osm if o.get("place_type") == "city"), None)
    state = next((o["name"] for o in osm if o.get("place_type") == "state"), None)
    country = next((o["name"] for o in osm if o.get("place_type") == "country"), None)
    # Fall back to flat fields if osm_locations absent
    city = city or location.get("city") or person.get("city")
    state = state or location.get("state") or person.get("state")
    country = country or location.get("country") or person.get("country")

    current_role = _get_current_role(person)

    full_name = person.get("full_name") or person.get("name") or ""
    name_parts = full_name.strip().split(" ", 1)
    first_name = person.get("first_name") or (name_parts[0] if name_parts else None)
    last_name = person.get("last_name") or (name_parts[1] if len(name_parts) > 1 else None)

    role_org = (current_role.get("organization") or {}) if current_role else {}
    company_li = (role_org.get("linkedin_info") or {}).get("public_profile_url")

    enriched = {
        "forager_id": person_id,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name or None,
        "linkedin_url": linkedin_url,
        "headline": person.get("headline"),
        "linkedin_handle": li_info.get("public_identifier"),
        "job_title": (current_role.get("title") or current_role.get("role_title")) if current_role else person.get("headline"),
        "company_name": _nested(current_role, "organization", "name") if current_role else None,
        "company_domain": _nested(current_role, "organization", "domain") if current_role else None,
        "company_linkedin_url": company_li,
        "role_start_date": current_role.get("start_date") if current_role else None,
        "city": city,
        "state": state,
        "country": country,
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


def extract_linkedin_company_slug(url: str) -> Optional[str]:
    """Extract the company slug from a LinkedIn company URL or return the slug as-is."""
    if not url:
        return None
    if "linkedin.com/company/" in url:
        slug = url.split("linkedin.com/company/")[-1].strip("/").split("/")[0].split("?")[0]
        return slug or None
    if "/" not in url:
        return url  # already a bare slug
    return None


def _build_linkedin_url(identifier: str) -> Optional[str]:
    if not identifier:
        return None
    if identifier.startswith("http"):
        return identifier
    return f"https://www.linkedin.com/in/{identifier}"


def _get_current_role(person: dict) -> Optional[dict]:
    roles = person.get("roles") or person.get("experience") or []
    current = [r for r in roles if r.get("is_current") or r.get("end_date") is None]
    if current:
        # When multiple current roles exist, pick the most recently started one
        return max(current, key=lambda r: r.get("start_date") or "")
    return roles[0] if roles else None


def _normalize_role_title(value: str) -> str:
    """Wrap plain text in double quotes for Forager boolean text search.
    If the user already included quotes or boolean operators, pass through as-is.
    """
    if not value:
        return value
    if '"' in value or ' AND ' in value or ' OR ' in value or ' NOT ' in value:
        return value
    return f'"{value}"'


def _extract_first_contact(data) -> Optional[str]:
    """Handle Forager contact lookup responses.

    Work emails / personal emails return a list of objects:
      [{"email": "...", "email_type": "...", "validation_status": "valid"}, ...]
    Phone numbers return a similar list of objects.
    """
    if isinstance(data, list):
        if not data:
            return None
        first = data[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return (
                first.get("email")
                or first.get("phone_number")
                or first.get("phone")
                or first.get("personal_email")
                or first.get("work_email")
            )
        return None
    if isinstance(data, dict):
        for key in ("work_emails", "personal_emails", "phone_numbers", "emails", "phones"):
            val = data.get(key)
            if isinstance(val, list) and val:
                return _extract_first_contact(val)
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
        # Domain rank: lower number = more authoritative site (like Alexa rank).
        # Heavily favour ranked domains; penalise null-rank GPT plugins / subdomains.
        rank = org.get("domain_rank")
        if rank and rank > 0:
            s += max(0, 60 - rank // 100)   # rank 242 → +57, rank 5000 → +10
        # Employee count as a tiebreaker
        employees = org.get("employees_amount") or 0
        s += min(employees // 1000, 10)
        return s

    return max(results, key=score)
