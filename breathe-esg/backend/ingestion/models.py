import uuid
from django.db import models
from django.contrib.auth.models import User


class Tenant(models.Model):
    """Root organization. All data belongs to exactly one tenant."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']


class UserProfile(models.Model):
    """Tenant association for Django's built-in auth.User."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} -> {self.tenant.name}"


class DataSource(models.Model):
    """Metadata about a data source (SAP plant, utility meter, travel program)."""
    SOURCE_TYPES = [
        ('SAP', 'SAP Fuel/Procurement'),
        ('UTILITY', 'Utility Electricity'),
        ('TRAVEL', 'Corporate Travel'),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    source_type = models.CharField(max_length=20, choices=SOURCE_TYPES)
    name = models.CharField(max_length=255)  # e.g., "SAP Production", "India Grid"
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.source_type})"

    class Meta:
        ordering = ['-created_at']
        unique_together = ('tenant', 'source_type', 'name')


class IngestionRun(models.Model):
    """Per-file ingestion summary: how many rows succeeded, failed, skipped."""
    STATUS = [
        ('PROCESSING', 'Processing'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    data_source = models.ForeignKey(DataSource, on_delete=models.CASCADE)
    file_name = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64)  # SHA256 for deduplication
    total_rows = models.IntegerField(default=0)
    parsed_rows = models.IntegerField(default=0)
    failed_rows = models.IntegerField(default=0)
    skipped_rows = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS, default='PROCESSING')
    ingested_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.file_name} ({self.status})"

    class Meta:
        ordering = ['-ingested_at']
        unique_together = ('tenant', 'file_hash')  # Prevent duplicate uploads


class RawRecord(models.Model):
    """Immutable archive: original row as received, never modified or deleted."""
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    ingestion_run = models.ForeignKey(IngestionRun, on_delete=models.CASCADE)
    source_type = models.CharField(max_length=20)
    raw_data = models.JSONField()  # entire original row
    row_number = models.IntegerField()  # line number in original file
    parse_error = models.TextField(null=True, blank=True)  # reason if failed or skipped
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        if self.parse_error:
            return f"RawRecord #{self.row_number} - Error: {self.parse_error[:50]}"
        return f"RawRecord #{self.row_number}"

    class Meta:
        ordering = ['row_number']
        indexes = [
            models.Index(fields=['tenant', 'ingestion_run']),
            models.Index(fields=['parse_error']),  # For finding failed rows
        ]


class NormalizedRecord(models.Model):
    """Parsed and calculated emissions record. Reviewable by analysts."""
    STATUS = [
        ('PENDING', 'Pending Review'),
        ('EDITED_PENDING', 'Edited - Pending Review'),
        ('APPROVED', 'Approved'),
        ('FLAGGED', 'Flagged'),
        ('REJECTED', 'Rejected (Legacy)'),
        ('LOCKED', 'Locked for Audit'),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    raw_record = models.OneToOneField(RawRecord, on_delete=models.CASCADE)
    source_type = models.CharField(max_length=20)
    scope = models.IntegerField(choices=[(1, 'Scope 1'), (2, 'Scope 2'), (3, 'Scope 3')])

    # Normalized activity data
    activity_value = models.DecimalField(max_digits=15, decimal_places=4, null=True, blank=True)
    activity_unit = models.CharField(max_length=20)  # 'L', 'kWh', 'km', 'nights'

    # Calculated CO2e
    co2e_kg = models.DecimalField(max_digits=15, decimal_places=4, null=True, blank=True)
    emission_factor = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    ef_source = models.CharField(max_length=100)  # e.g., 'DEFRA 2024', 'CEA 2023'

    # Period (actual dates, not month labels)
    period_start = models.DateField()
    period_end = models.DateField()

    # Review status
    status = models.CharField(max_length=20, choices=STATUS, default='PENDING')
    is_anomaly = models.BooleanField(default=False)

    # Locking
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='locked_records'
    )

    # Soft delete keeps the audit trail intact while hiding the row from review.
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='deleted_records'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_source_type_display()} - {self.activity_value} {self.activity_unit} - {self.co2e_kg} kg CO2e"

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'source_type', 'scope']),
            models.Index(fields=['is_anomaly']),
            models.Index(fields=['tenant', 'is_deleted']),
        ]


class AuditAction(models.Model):
    """Immutable log of every analyst action."""
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
    performed_by = models.ForeignKey(User, on_delete=models.CASCADE)
    performed_at = models.DateTimeField(auto_now_add=True)
    comment = models.TextField(null=True, blank=True)
    previous_value = models.JSONField(null=True, blank=True)  # for EDITED actions
    new_value = models.JSONField(null=True, blank=True)       # for EDITED actions

    def __str__(self):
        return f"{self.get_action_display()} by {self.performed_by.username} at {self.performed_at}"

    class Meta:
        ordering = ['-performed_at']
        indexes = [
            models.Index(fields=['tenant', 'performed_at']),
            models.Index(fields=['normalized_record', 'action']),
        ]
