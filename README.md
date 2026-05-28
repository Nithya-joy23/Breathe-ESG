# Breathe ESG â€” Carbon Emissions Ingestion & Review Platform

A Django REST + React application for ingesting, normalizing, and reviewing carbon emissions data from three enterprise sources: SAP (fuel), utility portals (electricity), and corporate travel platforms.

**Live Demo**: [Link will be added after deployment]

---

## Problem Statement

Enterprise carbon accounting is hard. Data lives everywhere:
- **Fuel consumption** in SAP (inconsistent units, German headers)
- **Electricity usage** from utility portals (billing periods misaligned to calendar months)
- **Business travel** in Concur/Navan (airport codes, cabin classes, hotel stays)

This app ingests messy data from all three sources, calculates CO2e emissions using published factors (DEFRA 2024 for flights, MoEFCC 2023 for SAP/travel hotels/ground transport, CEA 2023 for Indian electricity), and gives analysts a review dashboard to approve, flag, or edit records before they're locked for audit.

---

## Architecture

### Data Model

Three-layer design: immutable source of truth â†’ parsed/calculated records â†’ analyst review

- **RawRecord**: Original data as received (never modified, audit trail)
- **NormalizedRecord**: Parsed, normalized, calculated CO2e, reviewable by analysts
- **IngestionRun**: Per-file summary (parsed_rows, failed_rows, skipped_rows)
- **AuditAction**: Audit log (who approved/flagged/edited when)

See [MODEL.md](MODEL.md) for full schema and design rationale.

### Scope & Emission Factors

Emissions are categorized by GHG Protocol scope:
- **Scope 1** (Direct): All SAP data â†’ 2.68 kg CO2e/L for diesel, 2.04 for natural gas (MoEFCC 2023)
- **Scope 2** (Indirect Electricity): All utility data â†’ 0.233 kg CO2e/kWh (CEA 2023 India average)
- **Scope 3** (Value Chain): All travel data â†’ 0.255 kg CO2e/km (ECONOMY flights), 0.739 kg CO2e/km (BUSINESS flights), 20.8 kg CO2e/night (hotels), 0.171 kg CO2e/km (ground transport car)

### Parser Logic

Each source has custom parsing logic:

**SAP Flat File CSV**:
- German headers (Buchungsdatum, Werk, Menge, Meins, Bewegungsart)
- Movement type filtering (201=consumed, skip 101=goods receipt)
- Unit conversion (L, GAL, KL, M3 â†’ litres)
- Material code prefix matching (DIESEL*, NATGAS*)

**Utility Portal CSV**:
- Billing period dates (may not align to calendar months)
- Handles negative consumption (net metering with solar)
- Emission factor: grid-average CO2e per kWh

**Travel (Concur CSV)**:
- IATA airport code resolution â†’ Haversine distance calculation
- Cabin class multiplier (BUSINESS = 2.9Ã— ECONOMY)
- Hotel stays as separate records (per-night factor)
- Ground transport (taxi, train) intentionally skipped

See [SOURCES.md](SOURCES.md) for what real data looks like and why we chose these formats.

---

## Tech Stack

- **Backend**: Django 5.2 + Django REST Framework
- **Database**: PostgreSQL (production) / SQLite (development)
- **Frontend**: React (Vite)
- **Deployment**: Railway or Render
- **Authentication**: Django session auth (cookie-backed) with React login/logout

The local API uses a CSRF-exempt DRF session authentication class so React can call login, logout, and review actions with `axios.withCredentials` during prototype development. Logout flushes the Django session and expires `sessionid`/`csrftoken` cookies.

---

## Quick Start (Local Development)

### Backend Setup

```bash
cd breathe-esg/backend

# Create virtual environment
python -m venv venv
venv\Scripts\Activate.ps1  # Windows PowerShell

# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Create superuser (optional, for admin)
python manage.py createsuperuser

# Start dev server
python manage.py runserver
```

Backend runs on `http://localhost:8000`

### Frontend Setup

```bash
cd ../..
cd frontend

# Install dependencies
npm install

# Start dev server
npm run dev
```

Frontend runs on `http://localhost:5173`

---

## API Endpoints

### Upload Endpoints

```
POST /api/ingest/sap/              â€” Upload SAP fuel/procurement CSV
POST /api/ingest/utility/          â€” Upload utility electricity CSV
POST /api/ingest/travel/           â€” Upload Concur travel CSV
```

**Response** (201 Created):
```json
{
  "run_id": 1,
  "file_name": "sap_march_2024.csv",
  "total_rows": 50,
  "parsed_rows": 47,
  "failed_rows": 2,
  "skipped_rows": 1,
  "status": "COMPLETED"
}
```

### Record Endpoints

```
POST   /api/auth/login/                 - Sign in and create a Django session
POST   /api/auth/logout/                - Flush session and clear auth cookies
GET    /api/auth/me/                    - Return current authenticated user
GET    /api/records/                    - List records (filters: status, source_type, scope, is_anomaly)
PATCH  /api/records/<id>/approve/       - Approve record
PATCH  /api/records/<id>/reject/        - Flag record (requires comment)
PATCH  /api/records/<id>/edit/          - Edit activity_value (requires comment, recalc CO2e)
POST   /api/records/lock/               - Lock eligible APPROVED records (terminal)
GET    /api/records/summary/            - Scope 1/2/3 totals and status breakdown
GET    /api/runs/<run_id>/              - Get ingestion run details (failures)
```

---

## Data Flow Example

### SAP Upload

```
1. Analyst uploads sap_march_2024.csv (50 rows)
   â†“
2. Parser creates RawRecord for every row (immutable)
   â†“
3. For each RawRecord:
   - Check movement type (201 only)
   - Convert units to litres
   - Lookup emission factor by material code
   - Calculate CO2e_kg = activity_value Ã— ef
   - Create NormalizedRecord (status: PENDING)
   - Or set parse_error on RawRecord if fails
   â†“
4. IngestionRun summary:
   - total_rows: 50
   - parsed_rows: 47 (success)
   - failed_rows: 2 (parse errors)
   - skipped_rows: 0 (or more when source rows are intentionally skipped)
   â†“
5. Dashboard shows:
   - 47 PENDING records (ready for review)
   - 2 FAILED records (analyst must investigate)
   - Summary: Scope 1 CO2e breakdown
```

### Review Workflow

```
Analyst sees PENDING or FLAGGED records
   ->
Approve -> Record.status = APPROVED + AuditAction logged
Flag    -> Record.status = FLAGGED (excluded from totals) + comment logged
Edit    -> Change activity_value -> CO2e recalculated -> status = EDITED_PENDING
   ->
After review, click Lock -> eligible APPROVED records -> status = LOCKED (terminal)
   ->
Auditor sees LOCKED records with full trail (RawRecord + NormalizedRecord + AuditActions)
```

---

## Sample Data

Included in `/sample_data/`:

- `sap_export.csv` - 25 rows (mixed units, multiple date formats, unsupported units, negative quantity)
- `utility_export.csv` - 12 rows (billing periods Jan 5-Feb 4, 1 missing kWh)
- `travel_export.csv` - 15 rows (flights, hotels, 1 invalid airport code, 1 unsupported train mode)

To test locally:
```bash
# In Django shell
python manage.py shell
>>> from ingestion.models import Tenant, DataSource, IngestionRun
>>> tenant = Tenant.objects.create(name="Test Tenant")
>>> sap_source = DataSource.objects.create(tenant=tenant, source_type='SAP', name='SAP')
```

Then upload CSVs via API or Django admin.

---

## Status Lifecycle

```
PENDING          - Initial state after successful parse
   ->
[Analyst Reviews]
   |- APPROVED        - Analyst signs off
   |- FLAGGED         - Analyst flags for follow-up (excluded from totals)
   |- EDITED_PENDING  - Analyst changes value and sends it back for review
   ->
LOCKED           - Terminal (no unlock endpoint exists)
   ->
[Audit]
   -> Auditor sees full trail (RawRecord + decisions + audit log)
```

**Important**: Only APPROVED and LOCKED records count toward CO2e totals. Anomaly-warning records must have an explicit latest APPROVED review action before they can be locked.

---

## Multi-Tenancy

Every model has a `tenant` ForeignKey. Queries filter by the authenticated user profile tenant; local development also has a Default Tenant fallback.

Currently, all users are assigned to "Default Tenant" for simplicity. In production, add:
- User.tenant assignment
- Role-based access (Uploader, Analyst, Auditor, Admin)
- Django admin interface for tenant management

---

## Anomaly Detection

After each parse run completes, flag outliers:
- Compute mean + 2Ïƒ of co2e_kg across last 3 completed runs
- Flag any record in current run where co2e_kg > mean + 2Ïƒ
- Analyst sees warning indicator but decides: approve, flag, or edit

---

## Deployment

### Railway (Recommended)

1. Fork repository on GitHub
2. Create Railway project â†’ Add PostgreSQL plugin
3. Set environment variables:
   ```
   DATABASE_URL=postgresql://...
   SECRET_KEY=<random-secret>
   DEBUG=False
   ALLOWED_HOSTS=your-app.railway.app
   ```
4. Connect GitHub repo â†’ Railway auto-deploys
5. Run migrations: `railway run python manage.py migrate`
6. Create superuser: `railway run python manage.py createsuperuser`

### Render

Similar process. Use Render PostgreSQL add-on.

### Heroku (Legacy)

See Procfile for gunicorn command.

---

## What's NOT Implemented (See TRADEOFFS.md)

1. **Live SAP OData API** â€” Would require S/4HANA infrastructure + OAuth setup (2â€“3 days)
2. **Retroactive Emission Factor Updates** â€” If DEFRA publishes new factors, locked records aren't recalculated (audit integrity)
3. **Role-Based Access Control** â€” All users currently have analyst permissions (straightforward to add)

See [TRADEOFFS.md](TRADEOFFS.md) for full explanation and path to production.

---

## Files & Documentation

- [MODEL.md](MODEL.md) â€” Data schema, design rationale, GHG Protocol scope rules
- [DECISIONS.md](DECISIONS.md) â€” Why we chose SAP flat file vs OData, portal CSV vs PDF, Concur CSV vs API
- [SOURCES.md](SOURCES.md) â€” Real-world format research, sample data justification, production gaps
- [TRADEOFFS.md](TRADEOFFS.md) â€” What we didn't build and why

---

## Development Notes

### Adding a New Source Type

1. Create parser function in `ingestion/parsers.py`
2. Add upload endpoint in `ingestion/views.py`
3. Add URL route in `ingestion/urls.py`
4. Update `DataSource.SOURCE_TYPES` choices
5. Document scope assignment logic in MODEL.md

### Testing the Parsers

```python
# In Django shell
from ingestion.models import *
from ingestion.parsers import parse_sap
from django.core.files.base import ContentFile

tenant = Tenant.objects.get(name='Default Tenant')
sap_source = DataSource.objects.get(source_type='SAP')
run = IngestionRun.objects.create(
    tenant=tenant,
    data_source=sap_source,
    file_name='test.csv',
    file_hash='hash123',
    status='PROCESSING'
)

# With actual CSV file
with open('sample_data/sap_export.csv') as f:
    parse_sap(f, run, tenant)

print(f"Parsed: {run.parsed_rows}, Failed: {run.failed_rows}, Skipped: {run.skipped_rows}")
```

---

## Support & Questions

For questions on design decisions, see [DECISIONS.md](DECISIONS.md).
For research on data sources, see [SOURCES.md](SOURCES.md).
For architecture details, see [MODEL.md](MODEL.md).
