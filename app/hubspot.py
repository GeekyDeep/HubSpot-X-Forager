"""HubSpot CRM API client — creates/updates companies and contacts."""
import logging
import os
import httpx
from typing import Optional

log = logging.getLogger(__name__)

BASE_URL = "https://api.hubapi.com"

# HubSpot industry enum values (from their API validation error list)
_HUBSPOT_INDUSTRIES = {
    "ACCOUNTING", "AIRLINES_AVIATION", "ALTERNATIVE_DISPUTE_RESOLUTION",
    "ALTERNATIVE_MEDICINE", "ANIMATION", "APPAREL_FASHION",
    "ARCHITECTURE_PLANNING", "ARTS_AND_CRAFTS", "AUTOMOTIVE",
    "AVIATION_AEROSPACE", "BANKING", "BIOTECHNOLOGY", "BROADCAST_MEDIA",
    "BUILDING_MATERIALS", "BUSINESS_SUPPLIES_AND_EQUIPMENT", "CAPITAL_MARKETS",
    "CHEMICALS", "CIVIC_SOCIAL_ORGANIZATION", "CIVIL_ENGINEERING",
    "COMMERCIAL_REAL_ESTATE", "COMPUTER_NETWORK_SECURITY", "COMPUTER_GAMES",
    "COMPUTER_HARDWARE", "COMPUTER_NETWORKING", "COMPUTER_SOFTWARE", "INTERNET",
    "CONSTRUCTION", "CONSUMER_ELECTRONICS", "CONSUMER_GOODS", "CONSUMER_SERVICES",
    "COSMETICS", "DAIRY", "DEFENSE_SPACE", "DESIGN", "EDUCATION_MANAGEMENT",
    "E_LEARNING", "ELECTRICAL_ELECTRONIC_MANUFACTURING", "ENTERTAINMENT",
    "ENVIRONMENTAL_SERVICES", "EVENTS_SERVICES", "EXECUTIVE_OFFICE",
    "FACILITIES_SERVICES", "FARMING", "FINANCIAL_SERVICES", "FINE_ART",
    "FISHERY", "FOOD_BEVERAGES", "FOOD_PRODUCTION", "FUND_RAISING", "FURNITURE",
    "GAMBLING_CASINOS", "GLASS_CERAMICS_CONCRETE", "GOVERNMENT_ADMINISTRATION",
    "GOVERNMENT_RELATIONS", "GRAPHIC_DESIGN", "HEALTH_WELLNESS_AND_FITNESS",
    "HIGHER_EDUCATION", "HOSPITAL_HEALTH_CARE", "HOSPITALITY", "HUMAN_RESOURCES",
    "IMPORT_AND_EXPORT", "INDIVIDUAL_FAMILY_SERVICES", "INDUSTRIAL_AUTOMATION",
    "INFORMATION_SERVICES", "INFORMATION_TECHNOLOGY_AND_SERVICES", "INSURANCE",
    "INTERNATIONAL_AFFAIRS", "INTERNATIONAL_TRADE_AND_DEVELOPMENT",
    "INVESTMENT_BANKING", "INVESTMENT_MANAGEMENT", "JUDICIARY", "LAW_ENFORCEMENT",
    "LAW_PRACTICE", "LEGAL_SERVICES", "LEGISLATIVE_OFFICE",
    "LEISURE_TRAVEL_TOURISM", "LIBRARIES", "LOGISTICS_AND_SUPPLY_CHAIN",
    "LUXURY_GOODS_JEWELRY", "MACHINERY", "MANAGEMENT_CONSULTING", "MARITIME",
    "MARKET_RESEARCH", "MARKETING_AND_ADVERTISING",
    "MECHANICAL_OR_INDUSTRIAL_ENGINEERING", "MEDIA_PRODUCTION", "MEDICAL_DEVICES",
    "MEDICAL_PRACTICE", "MENTAL_HEALTH_CARE", "MILITARY", "MINING_METALS",
    "MOTION_PICTURES_AND_FILM", "MUSEUMS_AND_INSTITUTIONS", "MUSIC",
    "NANOTECHNOLOGY", "NEWSPAPERS", "NON_PROFIT_ORGANIZATION_MANAGEMENT",
    "OIL_ENERGY", "ONLINE_MEDIA", "OUTSOURCING_OFFSHORING",
    "PACKAGE_FREIGHT_DELIVERY", "PACKAGING_AND_CONTAINERS",
    "PAPER_FOREST_PRODUCTS", "PERFORMING_ARTS", "PHARMACEUTICALS", "PHILANTHROPY",
    "PHOTOGRAPHY", "PLASTICS", "POLITICAL_ORGANIZATION",
    "PRIMARY_SECONDARY_EDUCATION", "PRINTING", "PROFESSIONAL_TRAINING_COACHING",
    "PROGRAM_DEVELOPMENT", "PUBLIC_POLICY", "PUBLIC_RELATIONS_AND_COMMUNICATIONS",
    "PUBLIC_SAFETY", "PUBLISHING", "RAILROAD_MANUFACTURE", "RANCHING",
    "REAL_ESTATE", "RECREATIONAL_FACILITIES_AND_SERVICES", "RELIGIOUS_INSTITUTIONS",
    "RENEWABLES_ENVIRONMENT", "RESEARCH", "RESTAURANTS", "RETAIL",
    "SECURITY_AND_INVESTIGATIONS", "SEMICONDUCTORS", "SHIPBUILDING",
    "SPORTING_GOODS", "SPORTS", "STAFFING_AND_RECRUITING", "SUPERMARKETS",
    "TELECOMMUNICATIONS", "TEXTILES", "THINK_TANKS", "TOBACCO",
    "TRANSLATION_AND_LOCALIZATION", "TRANSPORTATION_TRUCKING_RAILROAD",
    "UTILITIES", "VENTURE_CAPITAL_PRIVATE_EQUITY", "VETERINARY", "WAREHOUSING",
    "WHOLESALE", "WINE_AND_SPIRITS", "WIRELESS", "WRITING_AND_EDITING",
    "MOBILE_GAMES",
}

# Manual overrides for Forager industries that don't normalize cleanly
_INDUSTRY_OVERRIDES = {
    "artificial intelligence": "COMPUTER_SOFTWARE",
    "machine learning": "COMPUTER_SOFTWARE",
    "information technology": "INFORMATION_TECHNOLOGY_AND_SERVICES",
    "it services and it consulting": "INFORMATION_TECHNOLOGY_AND_SERVICES",
    "mining & metals": "MINING_METALS",
    "hospital & health care": "HOSPITAL_HEALTH_CARE",
    "oil & energy": "OIL_ENERGY",
    "venture capital & private equity": "VENTURE_CAPITAL_PRIVATE_EQUITY",
    "staffing & recruiting": "STAFFING_AND_RECRUITING",
    "marketing & advertising": "MARKETING_AND_ADVERTISING",
    "food & beverages": "FOOD_BEVERAGES",
    "arts and crafts": "ARTS_AND_CRAFTS",
    "e-learning": "E_LEARNING",
}


def _map_industry(value: str) -> Optional[str]:
    """Map Forager free-text industry to HubSpot's enum. Returns None if no match."""
    if not value:
        return None
    lower = value.lower().strip()
    if lower in _INDUSTRY_OVERRIDES:
        return _INDUSTRY_OVERRIDES[lower]
    normalized = (
        value.upper()
        .replace(" & ", "_AND_")
        .replace(" AND ", "_AND_")
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace(",", "")
        .replace("(", "")
        .replace(")", "")
    )
    return normalized if normalized in _HUBSPOT_INDUSTRIES else None


_CUSTOM_CONTACT_PROPS = [
    {"name": "linkedin_headline",    "label": "LinkedIn Headline",    "type": "string", "fieldType": "text", "groupName": "contactinformation"},
    {"name": "linkedin_profile_url", "label": "LinkedIn Profile URL", "type": "string", "fieldType": "text", "groupName": "contactinformation"},
    {"name": "company_linkedin_url", "label": "Company LinkedIn URL", "type": "string", "fieldType": "text", "groupName": "contactinformation"},
    {"name": "role_start_date",      "label": "Role Start Date",      "type": "string", "fieldType": "text", "groupName": "contactinformation"},
    {"name": "forager_person_id",    "label": "Forager Person ID",    "type": "string", "fieldType": "text", "groupName": "contactinformation"},
    {"name": "forager_linkedin_bio",  "label": "Forager LinkedIn Bio",  "type": "string", "fieldType": "textarea", "groupName": "contactinformation"},
    {"name": "detailed_description",  "label": "Detailed Description",  "type": "string", "fieldType": "textarea", "groupName": "contactinformation"},
]
_custom_contact_props_ready = False

_CUSTOM_COMPANY_PROPS = [
    {
        "name": "forager_company_id",
        "label": "Forager Company ID",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
    },
    {
        "name": "employee_range",
        "label": "Employee Range",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
    },
    {
        "name": "operating_status",
        "label": "Operating Status",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
    },
    {
        "name": "company_keywords",
        "label": "Forager Keywords",
        "type": "string",
        "fieldType": "text",
        "groupName": "companyinformation",
    },
]
_custom_props_ready = False


def _ensure_custom_contact_props() -> None:
    global _custom_contact_props_ready
    if _custom_contact_props_ready:
        return
    url = f"{BASE_URL}/crm/v3/properties/contacts"
    with httpx.Client(timeout=15) as client:
        for prop in _CUSTOM_CONTACT_PROPS:
            resp = client.post(url, json=prop, headers=_headers())
            if resp.status_code not in (200, 201, 409):
                log.warning("Could not create contact property %s: %s", prop["name"], resp.text)
    _custom_contact_props_ready = True


def _ensure_custom_company_props() -> None:
    global _custom_props_ready
    if _custom_props_ready:
        return
    url = f"{BASE_URL}/crm/v3/properties/companies"
    with httpx.Client(timeout=15) as client:
        for prop in _CUSTOM_COMPANY_PROPS:
            resp = client.post(url, json=prop, headers=_headers())
            if resp.status_code not in (200, 201, 409):
                log.warning("Could not create HubSpot property %s: %s", prop["name"], resp.text)
    _custom_props_ready = True


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['HUBSPOT_ACCESS_TOKEN']}",
        "Content-Type": "application/json",
    }


# ── Companies ─────────────────────────────────────────────────────────────────

def upsert_company(enriched: dict, existing_id: Optional[str] = None) -> dict:
    """Create or update a HubSpot company from Forager enrichment data.

    Pass existing_id to force-update a known company (e.g. from a webhook) and
    skip the search entirely — prevents duplicate company creation loops.
    """
    _ensure_custom_company_props()
    if not existing_id:
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
        "numberofemployees": enriched.get("headcount"),
        "annualrevenue": enriched.get("revenue"),
        "industry": _map_industry(enriched.get("industry")),
        "founded_year": enriched.get("founded_year"),
        "city": enriched.get("city"),
        "state": enriched.get("state"),
        "country": enriched.get("country"),
        "phone": enriched.get("phone"),
        "website": enriched.get("website") or enriched.get("domain"),
        "linkedin_company_page": enriched.get("linkedin_url"),
        "forager_company_id": str(enriched["forager_id"]) if enriched.get("forager_id") else None,
        "employee_range": enriched.get("headcount_range"),
        "operating_status": enriched.get("operating_status"),
        "company_keywords": ", ".join(enriched["keywords"]) if enriched.get("keywords") else None,
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
        resp.raise_for_status()
        data = resp.json()
        return {"id": data["id"], "action": "created", "properties": data.get("properties", {})}


def _update_company(company_id: str, props: dict) -> dict:
    url = f"{BASE_URL}/crm/v3/objects/companies/{company_id}"
    with httpx.Client(timeout=15) as client:
        resp = client.patch(url, json={"properties": props}, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
        return {"id": data["id"], "action": "updated", "properties": data.get("properties", {})}


# ── Contacts ──────────────────────────────────────────────────────────────────

def upsert_contact(enriched: dict, company_id: Optional[str] = None) -> dict:
    """Create or update a HubSpot contact from Forager enrichment data."""
    _ensure_custom_contact_props()
    forager_id = str(enriched["forager_id"]) if enriched.get("forager_id") else None
    email = enriched.get("email")
    linkedin = enriched.get("linkedin_url")

    existing_id = None
    if forager_id:
        existing_id = _find_contact_by_forager_id(forager_id)
    if not existing_id and email:
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
        "firstname":            enriched.get("first_name"),
        "lastname":             enriched.get("last_name"),
        "email":                enriched.get("email"),
        "phone":                enriched.get("phone"),
        "jobtitle":             enriched.get("job_title"),
        "company":              enriched.get("company_name"),
        "city":                 enriched.get("city"),
        "state":                enriched.get("state"),
        "country":              enriched.get("country"),
        "website":              enriched.get("company_domain"),
        "linkedin_headline":    enriched.get("headline"),
        "forager_linkedin_bio": enriched.get("description"),
        "linkedin_profile_url": enriched.get("linkedin_url"),
        "company_linkedin_url": enriched.get("company_linkedin_url"),
        "role_start_date":      enriched.get("role_start_date"),
        "forager_person_id":    str(enriched["forager_id"]) if enriched.get("forager_id") else None,
        "detailed_description": enriched.get("about"),
    }.items() if v is not None}


def _find_contact_by_forager_id(forager_id: str) -> Optional[str]:
    url = f"{BASE_URL}/crm/v3/objects/contacts/search"
    payload = {
        "filterGroups": [{"filters": [{"propertyName": "forager_person_id", "operator": "EQ", "value": forager_id}]}],
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
    url = f"{BASE_URL}/crm/v3/objects/contacts/search"
    payload = {
        "filterGroups": [{"filters": [{"propertyName": "linkedin_profile_url", "operator": "EQ", "value": linkedin_url}]}],
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

def find_company_for_discover(domain: str = None, name: str = None) -> Optional[dict]:
    """Find a HubSpot company and return its stored forager_company_id for people discovery."""
    company_id = None
    if domain:
        company_id = _find_company_by_domain(domain)
    if not company_id and name:
        matches = search_companies_by_name(name)
        if matches:
            company_id = matches[0]["id"]
    if not company_id:
        return None
    url = f"{BASE_URL}/crm/v3/objects/companies/{company_id}"
    with httpx.Client(timeout=15) as client:
        resp = client.get(url, params={"properties": "name,domain,forager_company_id"}, headers=_headers())
        resp.raise_for_status()
        p = resp.json().get("properties", {})
        return {
            "hubspot_id": company_id,
            "name": p.get("name"),
            "domain": p.get("domain"),
            "forager_company_id": p.get("forager_company_id"),
        }


def search_companies_by_name(name: str) -> list[dict]:
    url = f"{BASE_URL}/crm/v3/objects/companies/search"
    payload = {
        "filterGroups": [{"filters": [{"propertyName": "name", "operator": "CONTAINS_TOKEN", "value": name}]}],
        "properties": ["name", "domain"],
        "limit": 20,
    }
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        return [
            {"id": r["id"], "name": r["properties"].get("name"), "domain": r["properties"].get("domain")}
            for r in resp.json().get("results", [])
        ]


def get_company_contacts(company_id: str) -> list[dict]:
    with httpx.Client(timeout=15) as client:
        assoc_url = f"{BASE_URL}/crm/v4/objects/companies/{company_id}/associations/contacts"
        resp = client.get(assoc_url, headers=_headers())
        resp.raise_for_status()
        contact_ids = [str(r["toObjectId"]) for r in resp.json().get("results", [])]

    if not contact_ids:
        return []

    batch_url = f"{BASE_URL}/crm/v3/objects/contacts/batch/read"
    payload = {
        "inputs": [{"id": cid} for cid in contact_ids],
        "properties": ["firstname", "lastname", "email", "phone", "jobtitle", "forager_person_id", "linkedin_profile_url"],
    }
    with httpx.Client(timeout=15) as client:
        resp = client.post(batch_url, json=payload, headers=_headers())
        resp.raise_for_status()
        results = []
        for r in resp.json().get("results", []):
            p = r.get("properties", {})
            if not p.get("forager_person_id"):
                continue
            results.append({
                "hubspot_id": r["id"],
                "name": f"{p.get('firstname') or ''} {p.get('lastname') or ''}".strip(),
                "job_title": p.get("jobtitle"),
                "email": p.get("email"),
                "phone": p.get("phone"),
                "forager_person_id": p.get("forager_person_id"),
                "linkedin_url": p.get("linkedin_profile_url"),
            })
        return results


def _get_contact_props(contact_id: str, properties: list[str]) -> dict:
    url = f"{BASE_URL}/crm/v3/objects/contacts/{contact_id}"
    with httpx.Client(timeout=15) as client:
        resp = client.get(url, params={"properties": ",".join(properties)}, headers=_headers())
        resp.raise_for_status()
        return resp.json().get("properties", {})


def _linkedin_handle(url: str) -> Optional[str]:
    if not url:
        return None
    # Strip trailing slash, then take last path segment
    parts = url.rstrip("/").split("/")
    return parts[-1] if parts else None
