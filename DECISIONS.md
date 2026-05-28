# DECISIONS.md â€” Key Decisions & Reasoning

## Overview

This document explains the ambiguities I faced and how I resolved them. It shows judgment, not just implementation.

---

## 1. SAP Source Format: Flat File CSV vs Live API

### The Question
SAP data can be extracted four ways: IDoc (EDI), OData (REST), BAPI (RFC), or Flat File (CSV export from GUI). Which one?

### My Decision: Flat File CSV

### Reasoning

**Why NOT IDoc**:
- IDoc is proprietary EDI (Electronic Data Interchange)
- Requires: S/4HANA system running IDoc layer, RFC connectivity, EDI subsystem configured
- For a prototype: I'd need a live SAP sandbox with EDI infrastructure = unrealistic

**Why NOT OData**:
- OData is a REST-like API layer on S/4HANA
- Requires: Live S/4HANA instance, OAuth credentials, OData layer running
- For a prototype: Credentials are tied to specific customer accounts, not something I can fabricate
- At scale: Yes, this is the "right" way. But today: Can't implement without live infrastructure

**Why NOT BAPI**:
- BAPI (Business Application Programming Interface) is not a file format
- Requires: RFC calls into live SAP system (pynwrfc library, connection details)
- For a prototype: Same problem as ODataâ€”requires live system access

**Why CSV**:
- CSV is exported from SAP transaction MB51 (Material Document list) by business users
- This is how it actually happens: Sustainability team emails asking, "Can you send me a CSV of fuel expenses?"
- No infrastructure required: Just download from GUI and email
- Defensible: "This is the lowest common denominator. Every SAP customer can do this."
- Testable: I can fabricate realistic data without live SAP

### Trade-off
In production, you'd want a live OData connector (scheduled job pulling real-time data). But for prototype, flat file is the right call.

---

## 2. Utility Source Format: Portal CSV vs PDF vs API

### The Question
How do facilities teams typically get utility consumption data? PDF bills, portal CSV export, or API?

### My Decision: Portal CSV Export

### Reasoning

**Why NOT PDF**:
- Utilities send bills as PDFs (standard)
- Extracting data from PDF requires: OCR (Optical Character Recognition), document parsing, handling different utility formats
- Problem: This becomes a document parsing problem, not an emissions accounting problem. Scope creep.
- For prototype: Adds complexity that doesn't teach about carbon accounting. Out of scope.

**Why NOT API**:
- Some utilities offer APIs: EnerNOC, Enel X, Sense, etc.
- Requires: OAuth credentials, API key tied to specific customer accounts
- For a prototype: I can't obtain credentials. Would need to mock the API.
- Trade-off: Mocking an API teaches less than handling real data problems (billing periods that don't align to calendar months, tariff structures, etc.)

**Why CSV**:
- Facilities managers log into utility portal â†’ download usage report â†’ email CSV to sustainability team
- This is what actually happens at scale
- No OAuth setup needed: Facilities manager just clicks and downloads
- Defensible: "This is what real companies do today"
- Realistic data problems: billing periods (Jan 5 â€“ Feb 8), missing readings, negative consumption (net metering with solar)

### Trade-off
You lose the ability to detect consumption anomalies in real-time (you're always one month behind). Production would want APIs. But for prototype, this is the realistic starting point.

---

## 3. Travel Source Format: Concur CSV vs Live API vs Manual Entry

### The Question
How do companies track business travel for emissions? Concur/Navan export, live API, or analyst enters manually?

### My Decision: Concur CSV Export

### Reasoning

**Why NOT Live API**:
- Concur and Navan both have REST APIs
- Requires: OAuth 2.0 enterprise credentials, no public sandbox available
- For a prototype: Can't obtain credentials. Would need to mock.
- Trade-off: Mocking teaches less than handling real data problems (missing airport codes, invalid cabin classes, incomplete hotel stays)

**Why NOT Manual Entry**:
- Could have analyst paste travel data into web form
- Problem: Doesn't teach about data ingestion, parsing, error handling, or source-of-truth tracking
- Also: Doesn't scale. Companies with 1000+ travelers can't manually enter every trip.

**Why CSV**:
- Travel managers in Concur run "Trip Summary" report â†’ download CSV â†’ email to sustainability team
- This is what real companies do
- Realistic data problems: IATA airport codes (must resolve to coordinates), cabin class multipliers, hotel stays as separate entries
- Defensible: "This is how enterprise travel programs work"

### Trade-off
You're batch processing travel data (month-to-month), not real-time. Production would want live API. But for prototype, this is the realistic starting point.

---

## 4. Scope Assignment: Automatic (Parser) vs Manual (User Input)

### The Question
Should analysts manually assign Scope 1/2/3, or should the parser infer it from the data source?

### My Decision: Parser Infers Scope (Automatic)

### Reasoning

**Why NOT Manual**:
- If analysts manually assign scope, you introduce human error
- Analysts might tag SAP data as Scope 2 (wrongâ€”SAP is fuel, = Scope 1)
- No way to audit: "Why did they assign it as Scope 3?"
- Doesn't scale: Every company has hundreds of records; manual assignment is tedious

**Why Automatic**:
- Scope is deterministic by source:
  - SAP = Scope 1 (direct fuel consumption)
  - Utility = Scope 2 (purchased electricity)
  - Travel = Scope 3 (business travel, value chain)
- No ambiguity: Parser sets scope, no user override possible
- Auditable: Scope is tied to ingestion logic, documented, repeatable

### Trade-off
You lose flexibility (e.g., what if a client uses SAP for electricity purchases, not fuel?). But for most companies, this is the right call. If a client has an edge case, it's a configuration issue (create a new DataSource with different logic), not a per-record decision.

---

## 5. Emission Factors: Where Do They Come From?

### The Question
Are emission factors hardcoded in the application, or configurable per tenant?

### My Decision: Hardcoded, Static, with Documentation

### Reasoning

**Why NOT per-tenant configuration**:
- Would add: Django admin interface, database table (FactorConfiguration), versioning logic
- Scope creep: 2â€“3 days of work for a prototype
- Most companies use published factors (DEFRA for UK, CEA for India, EPA for US)
- Configurable factors are a production feature, not prototype

**Why Static + Documented**:
- Use published factors: DEFRA 2024 for international flights, MoEFCC 2023 for SAP fuel and travel hotels/ground transport, CEA 2023 for Indian electricity
- Store on every NormalizedRecord: `emission_factor` + `ef_source` fields
- If published factors change next year, new ingestion run needed (don't recalculate locked recordsâ€”breaks audits)
- Documented in SOURCES.md: "In production, would need per-region, per-tariff factors"

### Trade-off
Rigid (same factors for all tenants). Production needs per-region, per-tariff factors. But for prototype, this is defensible.

---

## 6. Unit Normalization: What's the Standard Unit?

### The Question
SAP gives data in L, KL, GAL, M3. Should we normalize to one unit, or store raw + normalized?

### My Decision: Normalize to Standardized Units, Store Both

### Reasoning

| Source | Standard Unit | Why |
|--------|---------------|-----|
| SAP Fuel | Litres (L) | Cross-client comparable. 1 GAL = 3.78541 L. Works for all fuel types. |
| Utility | kWh | Only standard for electricity. Universal. |
| Travel Distance | Kilometers (km) | Cross-region comparable. Haversine gives km. |
| Travel Hotel | Nights | Can't reduce further. Emission factor is per-night. |

**Store both**:
- `raw_quantity` + `raw_unit` from original file (for auditing)
- `normalized_quantity` + `normalized_unit` on NormalizedRecord (for calculation)

### Trade-off
None. This is best practice: preserve original data, calculate on normalized.

---

## 7. Anomaly Detection: How Sensitive?

### The Question
How do you flag suspicious records without false positives?

### My Decision: Mean + 2Ïƒ Across Last 3 Runs

### Reasoning

**Why 2Ïƒ (not 1Ïƒ or 3Ïƒ)**:
- 1Ïƒ: 68% of data falls within 1Ïƒ. Flagging beyond 1Ïƒ = too many false positives
- 2Ïƒ: 95% of data falls within 2Ïƒ. Flagging beyond 2Ïƒ = catches real outliers (5% threshold)
- 3Ïƒ: 99.7% of data falls within 3Ïƒ. Too conservative; misses real anomalies

**Why last 3 runs (not all historical data)**:
- If you aggregate across all historical data, older runs skew the mean
- Example: Client implemented renewable energy â†’ recent runs have lower emissions â†’ old mean is obsolete
- Last 3 runs = ~3 months of data = recent enough to be relevant, large enough to be statistically meaningful

**Why flag but don't auto-reject**:
- Anomalies aren't necessarily errors. Example: Trip from Delhi to London is legitimately high-emission
- Analyst should see the warning, but decide: approve, flag, or edit

### Trade-off
Requires at least 3 historical runs before anomaly detection works. If this is the first month of ingestion, no flagging yet. In production, you'd load with historical data.

---

## 8. File Deduplication: SHA256 Hash

### The Question
How do you prevent the same file from being ingested twice?

### My Decision: SHA256 Hash on File Content

### Reasoning

**Why SHA256**:
- One-way hash: two identical files have same hash
- Collision-resistant: 2^256 possible hashes; chance of collision â‰ˆ 0
- Standard: Used for file integrity checking everywhere
- Stored on IngestionRun.file_hash

**When duplicate is detected**:
- Upload endpoint checks if hash already exists
- If yes: Return error, don't create new IngestionRun
- User gets message: "This file was already ingested on 2024-01-15"

### Trade-off
If analyst modifies file slightly (adds one row) and re-uploads, it's treated as different file. Not a problem: Changed data = legitimately different dataset.

---

## 9. Lock Mechanism: Terminal (No Unlock)

### The Question
Should locked records be editable/unlockable, or truly terminal?

### My Decision: Terminal (No Unlock)

### Reasoning

**Why Terminal**:
- ESG reports are submitted to auditors with locked records
- Auditor requires: "Show me what you locked and when"
- If analyst can unlock and re-edit post-lock, audit trail is compromised
- Realistic: Once you sign off on a report, you don't change it mid-audit

**If analyst finds error post-lock**:
- Create a new IngestionRun with corrected data
- New records are PENDING, analyst approves, lock again
- Auditor sees: Old locked records + new locked records + changelog

### Trade-off
Rigid (can't fix typos post-lock). But this is intentional: Freezes data for audit integrity.

---

## 10. Editing: What Happens When Analyst Edits?

### The Question
If analyst changes a normalized value, should status stay APPROVED or reset to PENDING?

### My Decision: Reset to PENDING

### Reasoning

**Why Reset**:
- Analyst edited activity_value â†’ recalculates co2e_kg
- New CO2e value hasn't been reviewed by anyone else
- Good practice: All changes go back to review queue
- Auditable: Full trail (AuditAction logged with previous_value + new_value)

**Example**:
```
1. Record is APPROVED: 5000 L diesel = 13,400 kg CO2e
2. Analyst edits: 4800 L (corrected based on source document)
3. Status â†’ PENDING (back to review)
4. AuditAction logged: previous_value=5000, new_value=4800, comment="Corrected per fuel receipt"
5. Reviewer approves new value
```

### Trade-off
Edit requires re-approval (slower). But this is safer: Prevents analysts from making bulk edits without peer review.

---

## 11. Comments: Which Actions Require Them?

### The Question
For which actions is a comment mandatory?

### My Decision: FLAGGED and EDITED Only

### Reasoning

| Action | Comment Required? | Why |
|--------|-------------------|-----|
| APPROVED | âœ— No | Record passed review; no explanation needed |
| FLAGGED | Yes | Analyst must explain why it needs follow-up (for auditor) |
| EDITED | âœ“ Yes | Analyst must explain what changed and why |
| LOCKED | No | Lock is automatic for eligible APPROVED records; no comment needed |

**Enforced at the API view level** (not database):
```python
# If action=FLAGGED or EDITED, return 400 if comment is null/empty.
```

### Trade-off
Comment field is optional at database level (nullable) but validated at API level. This prevents accidental locks but doesn't restrict querying.

---

## 12. Tenant Assignment: Request User vs Manual

### The Question
How do you assign a record to a tenant?

### My Decision: From the authenticated user profile tenant

### Reasoning

**Why NOT manual**:
- If endpoint lets analyst choose tenant, data can leak (User A uploads data, assigns to User B's tenant)
- Analyst might make mistake: "Upload to wrong tenant"

**Why FROM request.user**:
- Django's authentication layer gives `request.user`
- `request.user.profile.tenant` is where the current user belongs
- All records created during this request belong to that tenant
- Fail-safe: Can't accidentally cross-tenant data

### Trade-off
Requires authentication on all ingestion endpoints. But this is necessary for multi-tenancy anyway.

---

## 13. Error Handling: Fail Fast vs Continue?

### The Question
If one row fails to parse, should the entire ingestion abort or continue?

### My Decision: Continue (Fail Gracefully)

### Reasoning

**Why NOT Fail Fast**:
- If 1 row out of 1000 has a typo, don't abort entire upload
- Analyst would have to fix and re-upload all 1000 rows
- Unrealistic: Companies tolerate small error rates

**Why Continue**:
- Each row has try/except block
- Failed row: Create RawRecord with parse_error, increment failed_rows counter
- Continue to next row
- At end: IngestionRun shows status=COMPLETED, failed_rows=1, parsed_rows=999
- Analyst sees summary and can decide: "1% error rate is acceptable" or "I need to fix source data"

### Trade-off
Analyst must review failures manually (no bulk fix). But this is realistic: Data quality issues are discovered, not auto-resolved.

---

## Questions I'd Ask the PM (If I Could)

1. **Multi-region support**: Do you need grid factors for UK (0.207), US (0.4â€“0.6), or just India (0.233)? Affects utility parser.
2. **Historical data backfill**: Do clients have 5 years of historical data they want to ingest? If yes, need efficient bulk loading, not just web upload.
3. **Role-based access**: Should we support Uploader (ingest only), Analyst (review + approve), Auditor (read-only), or just single analyst role?
4. **Retroactive factor updates**: If DEFRA updates emission factors next year, should locked records be recalculated? Or leave as-is for audit trail?
5. **Ground transport**: Should we include taxi, train, and bus travel? Currently only car rows are calculated; unsupported modes are flagged.

