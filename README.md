# HubSpot × Forager Enrichment Automation

A FastAPI service that connects [Forager.ai](https://forager.ai) with HubSpot CRM to automatically enrich companies and contacts with live data — triggered by webhooks or on demand from a built-in UI.

Deployed on [Railway](https://railway.app).

---

## What it does

### Automatic enrichment (webhooks)
- **Company created in HubSpot** → the service instantly enriches it with Forager data: description, headcount, revenue, industry, location, LinkedIn URL, and more.
- **Contact created in HubSpot** → if a LinkedIn Profile URL is present, the service looks the person up in Forager and fills in job title, company, location, bio, and headline.

### Manual enrichment (UI)
A browser-based UI (served at `/`) provides two tabs:

**Discover Contacts**
- Search for a company by name or domain
- Browse employees page by page with a legitimacy filter that removes duplicate or stale profiles
- Select contacts and push them to HubSpot in one click — zero Forager credits used at this stage

**Enrich Contacts**
- Search for a company already in HubSpot
- View all associated contacts with their LinkedIn profiles
- Select contacts and enrich them with email and/or phone via three buttons: `Enrich Email`, `Enrich Phone`, `Enrich Email & Phone`

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, Pydantic, httpx |
| Frontend | Vanilla HTML/CSS/JavaScript (served by FastAPI) |
| Hosting | Railway |
| APIs | Forager.ai v2, HubSpot CRM v3/v4 |

---

## Project structure

```
forager-hubspot/
├── app/
│   ├── main.py          # FastAPI routes, webhook handler, enrichment logic
│   ├── hubspot.py       # HubSpot CRM API client (companies, contacts, associations)
│   ├── forager.py       # Forager API client (org search, person lookup, contact data)
│   └── static/
│       └── index.html   # Browser UI (Discover + Enrich tabs)
├── requirements.txt
├── Procfile
└── railway.toml
```

---

## Environment variables

All secrets are stored as environment variables — nothing is hard-coded. Set these in Railway (or a `.env` file for local development):

| Variable | Description |
|---|---|
| `FORAGER_API_KEY` | Your Forager.ai API key |
| `FORAGER_ACCOUNT_ID` | Your Forager.ai account ID |
| `HUBSPOT_ACCESS_TOKEN` | HubSpot Legacy App access token |
| `HUBSPOT_CLIENT_SECRET` | HubSpot app client secret (used to verify webhook signatures) |

Copy `.env.example` to `.env` and fill in your values for local development.

---

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/GeekyDeep/HubSpot-X-Forager.git
cd HubSpot-X-Forager
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env and fill in your API keys
```

### 3. Run locally

```bash
uvicorn app.main:app --reload --port 8080
```

Open `http://localhost:8080` in your browser.

### 4. Deploy to Railway

```bash
railway up
```

---

## HubSpot webhook setup

To enable automatic enrichment when records are created in HubSpot:

1. Go to **HubSpot → Settings → Integrations → Legacy Apps**
2. Open your app and navigate to the **Webhooks** tab
3. Set the target URL to:
   ```
   https://<your-railway-domain>/webhook/hubspot
   ```
4. Subscribe to these events:
   - `company.creation`
   - `contact.creation`

The service validates the webhook signature using `HUBSPOT_CLIENT_SECRET` when it is set.

---

## Key API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Browser UI |
| `GET` | `/health` | Health check |
| `POST` | `/webhook/hubspot` | HubSpot webhook receiver |
| `POST` | `/discover/people` | Browse employees at a Forager org (paginated) |
| `POST` | `/push/contacts` | Push discovered contacts to HubSpot (0 credits) |
| `POST` | `/search/company` | Look up a company in HubSpot by name or domain |
| `GET` | `/hubspot/companies` | Search HubSpot companies by name |
| `GET` | `/hubspot/companies/{id}/contacts` | Get contacts associated with a company |
| `POST` | `/enrich/contacts/email-phone` | Enrich selected contacts with email and/or phone |
| `POST` | `/enrich/company` | Manually enrich a company via Forager |
| `POST` | `/demo/openai` | Demo: enrich OpenAI + employees and push to HubSpot |

---

## How enrichment works

### Company enrichment
1. Webhook fires when a company is created in HubSpot
2. Service fetches the company's `name`, `domain`, and `linkedin_company_page` from HubSpot
3. Searches Forager by LinkedIn slug (preferred) or domain
4. Writes back: description, headcount, revenue, industry, city, state, country, LinkedIn URL, founded year
5. User-entered domain is always preserved — Forager's stored domain is never used to overwrite it

### Contact enrichment
1. Webhook fires when a contact is created in HubSpot
2. Service checks for `linkedin_profile_url` — skips if missing
3. Calls Forager's `person_detail_lookup` (1 credit) using the LinkedIn slug
4. Writes back: job title, company, location, bio, headline, Forager person ID
5. User-entered company name, website, and company LinkedIn URL are always preserved

### Email / phone lookup
- Uses Forager's `person_contacts_lookup` endpoints
- Requires a `forager_person_id` to already be set on the contact (populated during contact enrichment)
- Email lookup: up to 5 credits per person
- Phone lookup: up to 15 credits per person

---

## Legitimacy filtering

When browsing employees via Discover Contacts, profiles are automatically filtered for data quality. A profile is excluded if:

- Its role start date predates the company's founding date (impossible)
- The person's bio says "previously worked at [company]" while the role is marked as current
- The role description is an exact duplicate of another result on the same page (copy-paste fake profile)

Filtered profiles are returned alongside valid ones so you can review rejects before fetching the next page.
