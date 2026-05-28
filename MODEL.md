# MODEL.md â€” Data Model & Architecture

## Core Design Principle: Immutable Audit Trail

Every record in this system must be auditable. That means:
1. Original data is never modified â†’ `RawRecord` (immutable)
2. Parsed/calculated data is reviewable â†’ `NormalizedRecord` (with status lifecycle)
3. Every analyst action is logged â†’ `AuditAction` (who did what, when, why)

This separates **source of truth** (raw data) from **business logic** (calculations and decisions).

---

## Data Model

### 1. Tenant
```python
class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
```

Root organization. Every other model has `tenant` as a ForeignKey. Multi-tenancy is implemented as shared database with row-level filtering. **Rule: Every query must filter by the authenticated user profile tenant.**

### 2. DataSource
```python
class DataSource(models.Model):
    SOURCE_TYPES = [('SAP', 'SAP'), ('UTILITY', 'Utility'), ('TRAVEL', 'Travel')]
    
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    source_type = models.CharField(max_length=20, choices=SOURCE_TYPES)
    name = models.CharField(max_length=255)  # e.g., "SAP Production", "India Grid"
    created_at = models.DateTimeField(auto_now_add=True)
```

Metadata about each data source. Ties configuration (plant code mappings, emission factors) to a source. One `DataSource` per integration point.

### 3. IngestionRun
```python
class IngestionRun(models.Model):
    STATUS = [('PROCESSING', 'Processing'), ('COMPLETED', 'Completed'), ('FAILED', 'Failed')]
    
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    data_source = models.ForeignKey(DataSource, on_delete=models.CASCADE)
    file_name = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64)  # SHA256 for deduplication
    total_rows = models.IntegerField(default=0)  # rows in file
    parsed_rows = models.IntegerField(default=0)  # successful parses
    failed_rows = models.IntegerField(default=0)  # parse errors
    skipped_rows = models.IntegerField(default=0)  # intentional skips (e.g., movement_type 101)
    status = models.CharField(max_length=20, choices=STATUS)
    ingested_at = models.DateTimeField(auto_now_add=True)
```

One record per file upload. Tracks parse summary: how many rows succeeded, how many failed, how many were intentionally skipped.

**Key fields**:
- `file_hash`: SHA256 of file content. Reject if hash already exists (prevent duplicate ingestion).
- `failed_rows` vs `skipped_rows`: A failed row is an error (analyst must review). A skipped row is expected (e.g., SAP movement type 101 = goods receipt, not fuel consumption).

### 4. RawRecord
```python
class RawRecord(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    ingestion_run = models.ForeignKey(IngestionRun, on_delete=models.CASCADE)
    source_type = models.CharField(max_length=20)
    raw_data = models.JSONField()  # entire original row, never modified
    row_number = models.IntegerField()  # line number in original file
    parse_error = models.TextField(null=True, blank=True)  # reason if failed or skipped
    created_at = models.DateTimeField(auto_now_add=True)
```

**CRITICAL**: This is immutable. Created once, never updated. Stores the original row exactly as receivedâ€”German headers, inconsistent units, missing values, everything. This is the source of truth for auditors.

**When `parse_error` is set**:
- Row is a parse error (e.g., unrecognized unit) â†’ `failed_rows` counter increments
- Row is intentionally skipped (e.g., SAP movement type 101) â†’ `skipped_rows` counter increments, `parse_error='movement_type_skipped'`

If `parse_error` is null, the row successfully parsed and created a `NormalizedRecord`.

### 5. NormalizedRecord
```python
class NormalizedRecord(models.Model):
    STATUS = [
        ('PENDING', 'Pending Review'),
        ('APPROVED', 'Approved'),
        ('FLAGGED', 'Flagged'),
        ('REJECTED', 'Rejected (Legacy)'),
        ('EDITED_PENDING', 'Edited - Pending Review'),
        ('LOCKED', 'Locked for Audit'),
    ]
    
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    raw_record = models.OneToOneField(RawRecord, on_delete=models.CASCADE)
    source_type = models.CharField(max_length=20)
    scope = models.IntegerField(choices=[(1, 'Scope 1'), (2, 'Scope 2'), (3, 'Scope 3')])
    
    # Activity data (normalized)
    activity_value = models.DecimalField(max_digits=15, decimal_places=4)
    activity_unit = models.CharField(max_length=20)  # 'L', 'kWh', 'km', 'nights'
    
    # Calculated CO2e
    co2e_kg = models.DecimalField(max_digits=15, decimal_places=4)
    emission_factor = models.DecimalField(max_digits=15, decimal_places=6)
    ef_source = models.CharField(max_length=100)  # e.g., 'DEFRA 2024', 'CEA 2023'
    
    # Period
    period_start = models.DateField()
    period_end = models.DateField()
    
    # Analyst review
    status = models.CharField(max_length=20, choices=STATUS, default='PENDING')
    is_anomaly = models.BooleanField(default=False)  # flagged at ingestion time
    
    # Locking
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        'auth.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='locked_records'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
```

**OneToOneField to RawRecord**: Every normalized row traces back to exactly one original raw row. Immutable chain: raw â†’ parsed â†’ calculated.

**Key fields**:
- `scope`: Set by parser, never by user (Scope 1 = SAP, Scope 2 = Utility, Scope 3 = Travel)
- `activity_value` + `activity_unit`: The normalized quantity (always L, kWh, km, or nights)
- `co2e_kg`: Calculated = `activity_value Ã— emission_factor`
- `emission_factor` + `ef_source`: Stored for auditors. If DEFRA publishes new factors, locked records are NOT recalculated. This is deliberate.
- `period_start` + `period_end`: Never a month label. Actual dates (billing periods don't align to calendar months).
Flag is computed server-side and stored (`is_anomaly=True`). Analyst sees the warning indicator on dashboard but decides: approve, flag, or edit.
- `status` lifecycle: PENDING/EDITED_PENDING/FLAGGED -> APPROVED -> LOCKED (terminal, no unlock)

### 6. AuditAction
```python
class AuditAction(models.Model):
    ACTIONS = [
        ('APPROVED', 'Approved'),
        ('FLAGGED', 'Flagged'),
        ('REJECTED', 'Rejected'),
        ('EDITED', 'Edited'),
        ('LOCKED', 'Locked'),
        ('UNDO', 'Undo'),
        ('DELETED', 'Deleted'),
    ]
    
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    normalized_record = models.ForeignKey(NormalizedRecord, on_delete=models.CASCADE)
    action = models.CharField(max_length=20, choices=ACTIONS)
    performed_by = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    performed_at = models.DateTimeField(auto_now_add=True)
    comment = models.TextField(null=True, blank=True)
    previous_value = models.JSONField(null=True, blank=True)  # for EDITED actions
    new_value = models.JSONField(null=True, blank=True)       # for EDITED actions
```

Immutable log of every analyst action. `comment` is mandatory for FLAGGED and EDITED actions (enforced at the API view level).

**Example audit trail**:
```
1. 2024-01-15 10:30 â€” APPROVED by analyst@company.com
2. 2024-01-16 14:22 â€” EDITED by analyst@company.com (activity_value: 5000 â†’ 4800)
3. 2024-01-17 09:15 - FLAGGED by reviewer@company.com (comment: "Value mismatch with source document")
```

---

## Status Lifecycle

```
File uploaded
    â†“
RawRecord created (every row)
    â†“
Parse attempt
    â”œâ”€ Success â†’ NormalizedRecord created â†’ status: PENDING
    â””â”€ Error â†’ RawRecord.parse_error set â†’ no NormalizedRecord â†’ failed_rows++

Analyst review (PENDING records)
    â”œâ”€ Approve â†’ status: APPROVED â†’ AuditAction logged
    â”œâ”€ Flag -> status: FLAGGED -> AuditAction logged (comment required)
    â””â”€ Edit -> activity_value changed -> status: EDITED_PENDING (re-review) -> AuditAction logged (comment required, previous_value + new_value)

Lock (terminal)
    â”œâ”€ POST /api/records/lock/ -> eligible APPROVED records -> status: LOCKED
    â”œâ”€ locked_at: timestamp
    â”œâ”€ locked_by: user
    â””â”€ No unlock endpoint (irreversible)

Audit
    â””â”€ Auditor sees LOCKED records with full trail (RawRecord + NormalizedRecord + AuditActions)
```

**Only APPROVED and LOCKED records count toward CO2e totals. Anomaly-warning records require a latest explicit APPROVED action before locking.**

---

## Scope Assignment

**Rule: Scope is set by parser, never by user.**

- **Scope 1** (Direct Emissions): All SAP records
- **Scope 2** (Indirect from Electricity): All Utility records
- **Scope 3** (Value Chain): All Travel records

No manual override. No ambiguity.

---

## Emission Factors (MoEFCC 2023 + CEA 2023 + DEFRA 2024)

| Source | Fuel/Mode | Factor | Unit | Source |
|--------|-----------|--------|------|--------|
| SAP | DIESEL | 2.68 | kg CO2e/L | MoEFCC 2023 |
| SAP | NATGAS | 2.04 | kg CO2e/mÂ³ | MoEFCC 2023 |
| SAP | DEFAULT (unknown) | 2.68 | kg CO2e/L | MoEFCC 2023 (fallback) |
| Utility | Grid (India) | 0.233 | kg CO2e/kWh | CEA 2023 |
| Travel | Flight Economy | 0.255 | kg CO2e/km | DEFRA 2024 |
| Travel | Flight Business | 0.739 | kg CO2e/km | 0.255 Ã— 2.9 |
| Travel | Hotel | 20.8 | kg CO2e/night | MoEFCC 2023 |
| Travel | Ground transport car | 0.171 | kg CO2e/km | MoEFCC 2023 |

Stored on every NormalizedRecord so auditors know exactly which published factor was used.

---

## Anomaly Detection

Post-ingestion, flag outliers:

```python
# After a parse run completes:
# 1. Get last 3 completed IngestionRuns for this source_type
# 2. Compute mean + stddev of co2e_kg across those runs
# 3. Flag any NormalizedRecord in current run where co2e_kg > mean + 2*stddev
```

Flag is computed server-side and stored (`is_anomaly=True`). Analyst sees the warning indicator on dashboard but decides: approve, flag, or edit.

---

## Multi-Tenancy

Every model has `tenant` FK. **Rule: Every query must filter by the authenticated user profile tenant.**

```python
# View layer
queryset = NormalizedRecord.objects.filter(tenant=request.tenant)

# Never:
queryset = NormalizedRecord.objects.all()  # WRONG
```

No exceptions.

---

## Immutability & Audit Trail

| Entity | Immutable? | Why | Notes |
|--------|-----------|-----|-------|
| RawRecord | âœ“ Yes | Source of truth | Created once, never updated. Stores original data exactly. |
| NormalizedRecord | âœ“ Audited mutable | Calculated data | Can be edited, but every edit creates an AuditAction entry. Status changes are tracked. |
| AuditAction | âœ“ Yes | Audit log | Created once, never deleted. Chronological record. |
| IngestionRun | âœ“ Yes | Parse summary | Finalized once complete. Never modified. |

---

## Key Rules

1. **RawRecord = immutable archive**. Created once, never updated, never deleted.
2. **NormalizedRecord = mutable business logic**. Can edit, but every edit is logged (AuditAction).
3. **AuditAction = immutable audit trail**. Every touch (approve, flag, edit, lock, undo, delete) is logged with timestamp + user.
4. **Scope is parser logic**. Never user input.
5. **Emission factors are static**. If DEFRA updates, new ingestion run needed. Locked records not recalculated.
6. **Multi-tenancy is non-optional**. Every query filters by tenant. Local development also has a Default Tenant fallback.
7. **Lock is terminal**. No unlock endpoint.
8. **File deduplication by SHA256**. Same file uploaded twice = rejected as duplicate.
9. **Period dates are actual dates**. Never month labels. Billing cycles don't align to calendar months.
10. **Failed rows â‰  skipped rows**. Failed = error (analyst review). Skipped = expected (e.g., non-fuel SAP movement).

---

## Why This Design Works

1. **Auditable**: Auditors see original data (RawRecord) + calculated data (NormalizedRecord) + every decision (AuditAction).
2. **Traceable**: OneToOne link from NormalizedRecord â†’ RawRecord means every calculation is tied to source data.
3. **Defensible**: Locked records have timestamp + user + full trail. No changes after lock.
4. **Scalable**: Tenant filtering at query level handles multi-tenancy cleanly.
5. **Realistic**: Reflects actual ESG reporting workflows (upload, review, approve, lock for auditors).

