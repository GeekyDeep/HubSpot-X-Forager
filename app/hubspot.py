"""HubSpot CRM API client — creates/updates companies and contacts."""
import os
import httpx
from typing import Optional

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
        "numberofemployees": enriched.get("headcount"),
        "annualrevenue": enriched.get("revenue"),
        "industry": _map_industry(enriched.get("industry")),
        "city": enriched.get("city"),
        "state": enriched.get("state"),
        "country": enriched.get("country"),
        "phone": enriched.get("phone"),
        "website": enriched.get("website") or enriched.get("domain"),
        "linkedin_company_page": enriched.get("linkedin_url"),
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
    # LinkedIn handle property not available in all HubSpot portals; skip gracefully
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
