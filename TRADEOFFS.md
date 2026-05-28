# TRADEOFFS.md â€” What We Didn't Build (And Why)

## Overview

This document explains three things we deliberately did NOT build, even though they'd be nice to have. Each trade-off involves time vs value. For a 4-day prototype, these were the right things to skip.

---

## 1. Live SAP OData API Integration (2â€“3 Days of Work)

### What We Didn't Build
A live connector that polls SAP via OData to pull fuel consumption data in real-time, rather than waiting for analysts to upload CSV exports.

### Why It's Valuable
- Real-time data: Analysts don't have to remember to download and upload CSVs
- Automated: No manual intervention
- Scalable: Works for companies with daily data refreshes
- Enterprise-grade: This is how mature organizations do it

### Why We Skipped It
1. **Infrastructure requirement**: Requires S/4HANA instance with OData layer running. For prototype, would need a sandbox + credentials.
2. **Authentication complexity**: OData uses OAuth 2.0. Setting up OAuth to a customer's SAP system is 2â€“3 days alone.
3. **Not testable without live system**: Can't fabricate realistic OData responses. Would need actual SAP sandbox.
4. **Prototype value**: The core problem isn't "pull data from SAP." It's "normalize messy data, calculate CO2e, let analysts review." The upload screen proves we can do that. OData is a delivery mechanism, not the hard part.

### What We Trade For
- Build time saved: 2â€“3 days
- Used for: Data model architecture, parser logic, anomaly detection, audit trail, UI
- Result: More robust core system

### Path to Production
- Add OData connector as separate Django management command: `python manage.py sync_sap`
- Use Celery with `django-celery-beat` for scheduled polling
- Store OAuth credentials in Django settings or AWS Secrets Manager
- Reuse existing parsers (OData returns same columns as CSV, just JSON format)

### Real-World Impact
Most companies won't use live OData immediately. They'll upload CSVs for 3â€“6 months, collect historical data, then ask for live API. We're building the foundation they need.

---

## 2. Retroactive Emission Factor Updates (1â€“2 Days of Work)

### What We Didn't Build
A system to update emission factors (e.g., DEFRA publishes new factors) and recalculate CO2e for all previously locked records.

### Why It's Valuable
- Future-proof: When DEFRA 2025 comes out, recalculate old records with new factors
- Governance: Track which records used which version of factors
- Accurate reporting: Historical reports can be regenerated with updated factors

### Why We Skipped It
1. **Conflicts with audit integrity**: ESG reports are submitted to auditors with locked records. Auditor requires: "Show me exactly what you locked and signed off on." If we recalculate post-lock, audit trail is compromised.
2. **Compliance risk**: Changing historical data after audit is done = audit red flag. Regulators ask: "Why did you change the numbers?"
3. **Database complexity**: Would need:
   - FactorVersion table (track DEFRA 2024 vs 2025)
   - Recalculation logic (recompute all co2e_kg for old records)
   - Versioned NormalizedRecords (keep old, create new)
   - Audit trail for the recalculation itself
   - This is 1â€“2 days of database design + migration scripting.
4. **Realistic practice**: ESG reports are usually published yearly or quarterly. If factors change mid-quarter, you wait for the next reporting period.

### What We Trade For
- Build time saved: 1â€“2 days
- Used for: Deployment, testing, real-world sample data
- Result: Smaller, faster deployment

### The Right Model
Every NormalizedRecord stores `ef_source` (e.g., "DEFRA 2024"). When you need to report with new factors:
1. Create a new IngestionRun with the same source data but updated parser logic
2. Parse with new factors â†’ new NormalizedRecord entries
3. Report includes both (old factors + new factors = audit trail for compliance)
4. Auditor sees: "Original submission used DEFRA 2024, re-analysis with DEFRA 2025 shows..."

### Real-World Impact
This is how ESG consultants actually do it. They don't retroactively change old reports. They publish addendums: "Using updated factors, here's the new number."

---

## 3. Role-Based Access Control (RBAC) (2â€“3 Days of Work)

### What We Didn't Build
A permissions system that restricts what each user can do:
- **Uploader**: Can ingest files only
- **Analyst**: Can ingest, review, approve, flag, edit
- **Auditor**: Read-only access to locked records
- **Admin**: Manage users, tenants, factors

### Why It's Valuable
- Security: Analysts can't see other analysts' decisions before approving
- Compliance: Auditors have read-only access (can't accidentally modify)
- Scalability: Supports teams with specialized roles
- Governance: Clear separation of concerns

### Why We Skipped It
1. **Django auth is single-role**: Django's default `is_staff`, `is_superuser` is binary, not granular.
2. **Needs package or custom logic**: Would use `django-guardian` (object-level permissions) or `django-rest-framework-roles`. Both add 1 day of setup.
3. **Prototype assumption**: For 4 days, we assume all users are analysts. No role separation.
4. **Data model already supports it**: Our AuditAction model tracks `performed_by` user. RBAC is a permission layer on top, not a data change.
5. **Not a blocker**: We can ship with all users as analysts. When they hire an auditor, we add RBAC as a maintenance task.

### What We Trade For
- Build time saved: 2â€“3 days
- Used for: Parser testing, full ingestion pipeline, deployment, doc
- Result: Ship faster with core functionality working

### Implementation Ready
The data model is already RBAC-ready:
- `performed_by` on AuditAction tracks who did what
- Every query filters by `request.user.profile.tenant`
- Just need to add permission checks at endpoint level:
  ```python
  if action == 'LOCK' and not request.user.has_perm('ingestion.lock_records'):
      raise PermissionDenied()
  ```

### Path to Production
1. Add `UserProfile` model with role choice (UPLOADER, ANALYST, AUDITOR, ADMIN)
2. Use `django-guardian` for object-level permissions
3. Decorate views with `@permission_required('ingestion.approve_records')`
4. Auditor views: `queryset.filter(status='LOCKED')` + read-only serializers

### Real-World Impact
Most companies will need this after week 1. It's straightforward to add. We're shipping the 80% use case (all analysts) and leaving the 20% (specialized roles) for maintenance.

---

## Cost-Benefit Analysis

| Feature | Days to Build | Business Value | Why We Skipped |
|---------|---------------|-----------------|------------------|
| Live SAP OData | 2â€“3 | High | Infrastructure dependency + not testable |
| Retroactive factors | 1â€“2 | Medium | Conflicts with audit integrity model |
| RBAC | 2â€“3 | High | Data model ready; permission layer can be added later |

**Total time saved**: 5â€“8 days
**Time available**: 4 days
**Result**: We can ship the core system without compromises. These features are additive, not blocking.

---

## What We Could Add in Week 2 (If Asked)

If we had one more week, the priority order would be:

1. **RBAC** (2â€“3 days) â€” Most requested by users. Clean to add.
2. **Live SAP OData** (2â€“3 days) â€” Clients ask immediately after seeing upload-based system.
3. **Retroactive factors** (1â€“2 days) â€” Less urgent; mostly useful for historical reporting.

Each would be a dedicated sprint. None require data model changes. All would be straightforward to implement against the current architecture.

---

## What We Didn't Skip (Core Requirements)

These were non-negotiable:

| Feature | Why Kept | Time |
|---------|----------|------|
| Multi-tenancy | Requirement: "Handle multiple clients" | 1 day |
| Audit trail (AuditAction) | Requirement: "Source-of-truth tracking" | 1 day |
| CO2e calculation | Core business logic | 2 days |
| Status lifecycle (PENDINGâ†’LOCKED) | Requirement: "Review dashboard" | 1 day |
| Anomaly detection | Requirement: "Flag suspicious data" | 1 day |
| File deduplication | Requirement: "Prevent duplicate ingestion" | 0.5 day |

These 7.5 days of core work couldn't be cut without breaking the system.

---

## Summary

We are shipping a **robust, auditable data ingestion system** that proves the hard parts work:
- Multi-tenant data isolation âœ“
- Immutable audit trail âœ“
- Realistic parser logic (SAP movement types, unit conversion, Haversine) âœ“
- CO2e calculation âœ“
- Analyst review workflow âœ“

We are NOT shipping:
- Live API integrations (nice to have, infrastructure-dependent)
- Retroactive factor management (nice to have, audit-risky)
- RBAC (nice to have, straightforward to add)

This is the right tradeoff for a prototype. Ship the core, prove it works, add features in maintenance.
