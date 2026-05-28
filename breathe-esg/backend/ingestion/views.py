import hashlib
import uuid
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.db import connection, transaction, models

from .models import Tenant, DataSource, IngestionRun, RawRecord, NormalizedRecord, AuditAction, UserProfile
from .parsers import parse_sap, parse_utility, parse_travel
from .authentication import create_access_token
from .tenancy import (
    ensure_user_tenant,
    get_or_create_default_tenant,
    get_user_profile,
    resolve_request_tenant,
)


def no_store_response(data, response_status=status.HTTP_200_OK):
    response = Response(data, status=response_status)
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


def get_tenant(request):
    """Get tenant from request middleware/profile, with a local demo fallback."""
    return getattr(request, 'tenant', None) or resolve_request_tenant(request)


def get_actor(request):
    """Return the authenticated user, or a demo user for the local prototype."""
    if request.user and request.user.is_authenticated:
        ensure_user_tenant(request.user)
        return request.user
    actor, _ = User.objects.get_or_create(username='demo-analyst')
    tenant = get_tenant(request)
    UserProfile.objects.get_or_create(user=actor, defaults={'tenant': tenant})
    return actor


@api_view(['POST'])
@permission_classes([AllowAny])
@csrf_exempt
def login_view(request):
    username = request.data.get('username', '').strip()
    password = request.data.get('password', '').strip()
    if not username or not password:
        return Response({'error': 'Username and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

    user = authenticate(request, username=username, password=password)
    if user is None:
        return Response({'error': 'Invalid username or password.'}, status=status.HTTP_400_BAD_REQUEST)
    if not user.is_active:
        return Response({'error': 'User account is disabled.'}, status=status.HTTP_403_FORBIDDEN)

    login(request, user)
    tenant = ensure_user_tenant(user) or get_tenant(request)
    return no_store_response({
        'username': user.username,
        'tenant_name': tenant.name,
        'tenant_id': str(tenant.id),
        'access_token': create_access_token(user),
    })


@api_view(['POST'])
@permission_classes([AllowAny])
@csrf_exempt
def register_view(request):
    username = request.data.get('username', '').strip()
    password = request.data.get('password', '').strip()

    if not username or not password:
        return Response({'error': 'Username and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

    if User.objects.filter(username=username).exists():
        return Response({'error': 'Username already exists.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            user = User.objects.create_user(username=username, password=password)
            
        return Response({'message': 'Registration successful'}, status=status.HTTP_201_CREATED)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@csrf_exempt
def logout_view(request):
    logout(request)
    request.session.flush()
    response = no_store_response({'success': True})
    response.delete_cookie(settings.SESSION_COOKIE_NAME)
    response.delete_cookie(settings.CSRF_COOKIE_NAME)
    return response


@api_view(['GET'])
def current_user(request):
    if not request.user or not request.user.is_authenticated:
        return no_store_response({'user': None}, status.HTTP_401_UNAUTHORIZED)
    tenant = ensure_user_tenant(request.user) or get_tenant(request)
    return no_store_response({
        'username': request.user.username,
        'tenant_name': tenant.name,
        'tenant_id': str(tenant.id),
    })


def serialize_audit_action(action):
    return {
        'id': action.id,
        'action': action.action,
        'performed_by': action.performed_by.username if action.performed_by else None,
        'performed_at': str(action.performed_at),
        'comment': action.comment,
        'previous_value': action.previous_value,
        'new_value': action.new_value,
    }


def build_anomaly_reasons(record, source_averages=None):
    source_averages = source_averages or {}
    reasons = []
    co2e = float(record.co2e_kg) if record.co2e_kg is not None else None
    activity = float(record.activity_value) if record.activity_value is not None else None
    factor = float(record.emission_factor) if record.emission_factor is not None else None
    source_avg = source_averages.get(record.source_type)

    if co2e is not None and source_avg and co2e > source_avg * 10:
        reasons.append(f'CO2e is more than 10x the {record.source_type} average ({source_avg:.1f} kg).')

    large_activity_limits = {
        'L': 5000000,
        'kWh': 1000000,
        'km': 50000,
        'nights': 365,
    }
    limit = large_activity_limits.get(record.activity_unit)
    if activity is not None and limit is not None and activity >= limit:
        reasons.append(f'Activity quantity {activity:g} {record.activity_unit} is unusually large for one record.')

    factor_ranges = {
        'L': (0.5, 5.0),
        'kWh': (0.01, 2.0),
        'km': (0.01, 1.5),
        'nights': (5.0, 100.0),
    }
    expected_range = factor_ranges.get(record.activity_unit)
    if factor is not None and expected_range:
        low, high = expected_range
        if factor < low or factor > high:
            reasons.append(f'Emission factor {factor:g} is outside expected range {low:g}-{high:g} for {record.activity_unit}.')

    if record.is_anomaly and not reasons:
        reasons.append('Marked by parser as suspicious for analyst review.')

    return reasons


def get_source_averages(tenant):
    return {
        item['source_type']: float(item['avg'] or 0)
        for item in NormalizedRecord.objects.filter(
            tenant=tenant,
            is_deleted=False,
            co2e_kg__isnull=False
        ).values('source_type').annotate(avg=models.Avg('co2e_kg'))
    }


def requires_explicit_approval_for_lock(record, source_averages=None):
    return record.is_anomaly or bool(build_anomaly_reasons(record, source_averages))


def has_explicit_approval(record):
    latest_review_action = AuditAction.objects.filter(
        tenant=record.tenant,
        normalized_record=record,
        action__in=['APPROVED', 'FLAGGED', 'REJECTED', 'EDITED', 'UNDO'],
    ).order_by('-performed_at').first()
    return latest_review_action is not None and latest_review_action.action == 'APPROVED'


def can_lock_record(record, source_averages=None):
    if record.status != 'APPROVED' or record.is_deleted:
        return False
    if requires_explicit_approval_for_lock(record, source_averages):
        return has_explicit_approval(record)
    return True


def build_normalization_steps(record):
    if record.activity_value is None or record.emission_factor is None or record.co2e_kg is None:
        return ['CO2e unavailable because activity or emission factor is missing.']
    return [
        f'{record.activity_value:g} {record.activity_unit} x {record.emission_factor:g} kg/{record.activity_unit} = {record.co2e_kg:g} kg CO2e'
    ]


def serialize_record(record, edited_record_ids=None, source_averages=None):
    edited_record_ids = edited_record_ids or set()
    raw_data = record.raw_record.raw_data if record.raw_record_id else None
    travel_date = raw_data.get('travel_date') if record.source_type == 'TRAVEL' and raw_data else None
    period_display = (
        travel_date or str(record.period_start)
        if record.source_type == 'TRAVEL' or record.period_start == record.period_end
        else f'{record.period_start} to {record.period_end}'
    )
    anomaly_reasons = build_anomaly_reasons(record, source_averages)
    return {
        'id': record.id,
        'raw_record_id': record.raw_record_id,
        'source_type': record.source_type,
        'scope': record.scope,
        'activity_value': float(record.activity_value) if record.activity_value is not None else None,
        'activity_unit': record.activity_unit,
        'co2e_kg': float(record.co2e_kg) if record.co2e_kg is not None else None,
        'emission_factor': float(record.emission_factor) if record.emission_factor is not None else None,
        'ef_source': record.ef_source,
        'period_start': str(record.period_start),
        'period_end': str(record.period_end),
        'travel_date': travel_date,
        'period_display': period_display,
        'status': record.status,
        'is_anomaly': record.is_anomaly or bool(anomaly_reasons),
        'anomaly_reasons': anomaly_reasons,
        'is_edited': record.id in edited_record_ids,
        'is_deleted': record.is_deleted,
        'raw_data': raw_data,
        'normalization_steps': build_normalization_steps(record),
        'source_file_name': record.raw_record.ingestion_run.file_name if record.raw_record_id else None,
        'ingested_at': str(record.raw_record.ingestion_run.ingested_at) if record.raw_record_id else None,
        'locked_at': str(record.locked_at) if record.locked_at else None,
        'locked_by': record.locked_by.username if record.locked_by else None,
        'deleted_at': str(record.deleted_at) if record.deleted_at else None,
        'deleted_by': record.deleted_by.username if record.deleted_by else None,
        'created_at': str(record.created_at),
    }


def record_audit_value(record):
    return {
        'status': record.status,
        'activity_value': float(record.activity_value) if record.activity_value is not None else None,
        'activity_unit': record.activity_unit,
        'co2e_kg': float(record.co2e_kg) if record.co2e_kg is not None else None,
        'emission_factor': float(record.emission_factor) if record.emission_factor is not None else None,
        'ef_source': record.ef_source,
        'is_deleted': record.is_deleted,
        'locked_at': str(record.locked_at) if record.locked_at else None,
        'locked_by': record.locked_by.username if record.locked_by else None,
    }


def get_or_create_data_source(tenant, source_type):
    """Get or create a DataSource for the given type."""
    source_name = dict(DataSource.SOURCE_TYPES).get(source_type, source_type)
    data_source, _ = DataSource.objects.get_or_create(
        tenant=tenant,
        source_type=source_type,
        defaults={'name': source_name}
    )
    return data_source


def calculate_file_hash(file):
    """Calculate SHA256 hash of file content."""
    file.seek(0)
    file_hash = hashlib.sha256(file.read()).hexdigest()
    file.seek(0)
    return file_hash


def check_duplicate_file(tenant, file_hash):
    """Check if file has already been ingested."""
    return IngestionRun.objects.filter(tenant=tenant, file_hash=file_hash).exists()


def build_upload_response(run):
    failed_records = RawRecord.objects.filter(
        ingestion_run=run,
        parse_error__isnull=False
    ).values('row_number', 'parse_error', 'raw_data')
    return {
        'run_id': str(run.id),
        'file_name': run.file_name,
        'total_rows': run.total_rows,
        'parsed_rows': run.parsed_rows,
        'failed_rows': run.failed_rows,
        'skipped_rows': run.skipped_rows,
        'status': run.status,
        'failures': list(failed_records),
    }


def dedupe_demo_run(run):
    seen_rows = set()
    for raw in RawRecord.objects.filter(ingestion_run=run).order_by('row_number', 'id'):
        if raw.row_number in seen_rows:
            raw.delete()
        else:
            seen_rows.add(raw.row_number)


def ensure_demo_source_records(tenant):
    """Keep local demo tabs populated without touching uploaded customer data."""
    for run in IngestionRun.objects.filter(
        tenant=tenant,
        file_hash__in=['demo-utility-review-queue', 'demo-travel-review-queue']
    ):
        dedupe_demo_run(run)

    utility_source = get_or_create_data_source(tenant, 'UTILITY')
    utility_run, _ = IngestionRun.objects.get_or_create(
        tenant=tenant,
        file_hash='demo-utility-review-queue',
        defaults={
            'data_source': utility_source,
            'file_name': 'demo_utility_export.csv',
            'total_rows': 4,
            'parsed_rows': 3,
            'failed_rows': 1,
            'status': 'COMPLETED',
        }
    )
    utility_rows = [
        (1, {'meter_id': 'MTR-IN-001', 'period_start': '2024-04-15', 'period_end': '2024-05-14', 'kwh': '14200'}, 14200, Decimal('0.233000'), 'APPROVED', False),
        (2, {'meter_id': 'MTR-IN-002', 'period_start': '2024-05-15', 'period_end': '2024-06-14', 'kwh': '50000'}, 50000, Decimal('0.233000'), 'FLAGGED', True),
        (3, {'meter_id': 'MTR-IN-003', 'period_start': '2024-06-18', 'period_end': '2024-07-17', 'kwh': '9800'}, 9800, Decimal('0.233000'), 'PENDING', False),
    ]
    for row_number, raw_data, kwh, factor, row_status, is_anomaly in utility_rows:
        raw, _ = RawRecord.objects.update_or_create(
            tenant=tenant,
            ingestion_run=utility_run,
            row_number=row_number,
            defaults={'source_type': 'UTILITY', 'raw_data': raw_data, 'parse_error': None}
        )
        record, created = NormalizedRecord.objects.get_or_create(
            tenant=tenant,
            raw_record=raw,
            defaults={
                'source_type': 'UTILITY',
                'scope': 2,
                'activity_value': Decimal(str(kwh)),
                'activity_unit': 'kWh',
                'co2e_kg': Decimal(str(kwh)) * factor,
                'emission_factor': factor,
                'ef_source': 'CEA 2023',
                'period_start': raw_data['period_start'],
                'period_end': raw_data['period_end'],
                'status': row_status,
                'is_anomaly': is_anomaly,
            }
        )
        if not created:
            record.source_type = 'UTILITY'
            record.scope = 2
            record.activity_value = Decimal(str(kwh))
            record.activity_unit = 'kWh'
            record.co2e_kg = Decimal(str(kwh)) * factor
            record.emission_factor = factor
            record.ef_source = 'CEA 2023'
            record.period_start = raw_data['period_start']
            record.period_end = raw_data['period_end']
            record.is_anomaly = is_anomaly
            record.save()

    RawRecord.objects.get_or_create(
        tenant=tenant,
        ingestion_run=utility_run,
        row_number=4,
        defaults={
            'source_type': 'UTILITY',
            'raw_data': {'meter_id': 'MTR-IN-004', 'period_start': '2024-07-18', 'period_end': '2024-08-17', 'unit': 'MMBTU'},
            'parse_error': 'Unrecognized unit: MMBTU',
        }
    )

    if not IngestionRun.objects.filter(tenant=tenant, file_hash='demo-travel-review-queue').exists():
        travel_source = get_or_create_data_source(tenant, 'TRAVEL')
        travel_run, _ = IngestionRun.objects.get_or_create(
            tenant=tenant,
            file_hash='demo-travel-review-queue',
            defaults={
                'data_source': travel_source,
                'file_name': 'demo_travel_export.csv',
                'total_rows': 5,
                'parsed_rows': 4,
                'failed_rows': 1,
                'status': 'COMPLETED',
            }
        )
        travel_rows = [
            (1, {'trip_id': 'TRV-DEMO-001', 'travel_date': '2024-04-25', 'mode': 'FLIGHT', 'origin': 'BOM', 'destination': 'LHR', 'distance_km': '7200'}, 7200, 'km', Decimal('0.255000'), 'PENDING'),
            (2, {'trip_id': 'TRV-DEMO-002', 'travel_date': '2024-05-03', 'mode': 'FLIGHT', 'origin': 'DEL', 'destination': 'JFK', 'distance_km': '11750'}, 11750, 'km', Decimal('0.739000'), 'FLAGGED'),
            (3, {'trip_id': 'TRV-DEMO-003', 'travel_date': '2024-05-09', 'mode': 'HOTEL', 'hotel_nights': '3', 'hotel_name': 'London City Hotel'}, 3, 'nights', Decimal('20.800000'), 'PENDING'),
            (4, {'trip_id': 'TRV-DEMO-004', 'travel_date': '2024-05-12', 'mode': 'CAR', 'distance_km': '180'}, 180, 'km', Decimal('0.171000'), 'PENDING'),
        ]
        for row_number, raw_data, activity, unit, factor, row_status in travel_rows:
            raw, _ = RawRecord.objects.get_or_create(
                tenant=tenant,
                ingestion_run=travel_run,
                row_number=row_number,
                defaults={'source_type': 'TRAVEL', 'raw_data': raw_data}
            )
            NormalizedRecord.objects.get_or_create(
                tenant=tenant,
                raw_record=raw,
                defaults={
                    'source_type': 'TRAVEL',
                    'scope': 3,
                    'activity_value': Decimal(str(activity)),
                    'activity_unit': unit,
                    'co2e_kg': Decimal(str(activity)) * factor,
                    'emission_factor': factor,
                    'ef_source': 'MoEFCC 2023' if unit != 'km' or raw_data.get('mode') != 'FLIGHT' else 'DEFRA 2024',
                    'period_start': raw_data['travel_date'],
                    'period_end': raw_data['travel_date'],
                    'status': row_status,
                    'is_anomaly': row_status == 'FLAGGED',
                }
            )
        RawRecord.objects.get_or_create(
            tenant=tenant,
            ingestion_run=travel_run,
            row_number=5,
            defaults={
                'source_type': 'TRAVEL',
                'raw_data': {'trip_id': 'TRV-DEMO-005', 'travel_date': '2024-05-14', 'mode': 'FLIGHT', 'origin': 'ZZZ', 'destination': 'LHR'},
                'parse_error': 'Unknown airport code: ZZZ',
            }
        )


@api_view(['POST'])
def upload_sap(request):
    """Upload SAP fuel/procurement data."""
    file = request.FILES.get('file')
    if not file:
        return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

    tenant = get_tenant(request)
    file_hash = calculate_file_hash(file)

    if check_duplicate_file(tenant, file_hash):
        return Response({
            'error': 'Duplicate file',
            'message': 'This file has already been ingested'
        }, status=status.HTTP_400_BAD_REQUEST)

    data_source = get_or_create_data_source(tenant, 'SAP')
    run = IngestionRun.objects.create(
        tenant=tenant,
        data_source=data_source,
        file_name=file.name,
        file_hash=file_hash,
        status='PROCESSING'
    )

    try:
        with transaction.atomic():
            parse_sap(file, run, tenant)
            run.status = 'COMPLETED'
            run.save()

        return Response(build_upload_response(run), status=status.HTTP_201_CREATED)

    except Exception as e:
        run.status = 'FAILED'
        run.save()
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def upload_utility(request):
    """Upload utility electricity data."""
    file = request.FILES.get('file')
    if not file:
        return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

    tenant = get_tenant(request)
    file_hash = calculate_file_hash(file)

    if check_duplicate_file(tenant, file_hash):
        return Response({
            'error': 'Duplicate file',
            'message': 'This file has already been ingested'
        }, status=status.HTTP_400_BAD_REQUEST)

    data_source = get_or_create_data_source(tenant, 'UTILITY')
    run = IngestionRun.objects.create(
        tenant=tenant,
        data_source=data_source,
        file_name=file.name,
        file_hash=file_hash,
        status='PROCESSING'
    )

    try:
        with transaction.atomic():
            parse_utility(file, run, tenant)
            run.status = 'COMPLETED'
            run.save()

        return Response(build_upload_response(run), status=status.HTTP_201_CREATED)

    except Exception as e:
        run.status = 'FAILED'
        run.save()
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def upload_travel(request):
    """Upload corporate travel data."""
    file = request.FILES.get('file')
    if not file:
        return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)

    tenant = get_tenant(request)
    file_hash = calculate_file_hash(file)

    if check_duplicate_file(tenant, file_hash):
        return Response({
            'error': 'Duplicate file',
            'message': 'This file has already been ingested'
        }, status=status.HTTP_400_BAD_REQUEST)

    data_source = get_or_create_data_source(tenant, 'TRAVEL')
    run = IngestionRun.objects.create(
        tenant=tenant,
        data_source=data_source,
        file_name=file.name,
        file_hash=file_hash,
        status='PROCESSING'
    )

    try:
        with transaction.atomic():
            parse_travel(file, run, tenant, actor=get_actor(request))
            run.status = 'COMPLETED'
            run.save()

        return Response(build_upload_response(run), status=status.HTTP_201_CREATED)

    except Exception as e:
        run.status = 'FAILED'
        run.save()
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def list_records(request):
    """List normalized records with optional filtering."""
    tenant = get_tenant(request)
    ensure_demo_source_records(tenant)
    include_deleted = request.query_params.get('include_deleted') == 'true'
    queryset = NormalizedRecord.objects.filter(tenant=tenant).select_related(
        'raw_record__ingestion_run', 'locked_by', 'deleted_by'
    ).order_by('-created_at')
    if not include_deleted:
        queryset = queryset.filter(is_deleted=False)

    status_filter = request.query_params.get('status')
    source_type = request.query_params.get('source_type')
    scope = request.query_params.get('scope')
    is_anomaly = request.query_params.get('is_anomaly')

    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if source_type:
        queryset = queryset.filter(source_type=source_type)
    if scope:
        queryset = queryset.filter(scope=int(scope))
    if is_anomaly is not None:
        queryset = queryset.filter(is_anomaly=is_anomaly.lower() == 'true')

    edited_record_ids = set(
        AuditAction.objects.filter(
            tenant=tenant,
            normalized_record__in=queryset,
            action='EDITED'
        ).values_list('normalized_record_id', flat=True)
    )
    source_averages = get_source_averages(tenant)

    data = [serialize_record(r, edited_record_ids, source_averages) for r in queryset[:1000]]

    return Response({
        'count': queryset.count(),
        'results': data
    })


@api_view(['PATCH'])
def approve_record(request, pk):
    """Approve a reviewable record."""
    tenant = get_tenant(request)
    try:
        record = NormalizedRecord.objects.get(id=pk, tenant=tenant, is_deleted=False)
    except NormalizedRecord.DoesNotExist:
        return Response({'error': 'Record not found'}, status=status.HTTP_404_NOT_FOUND)

    if record.status not in ['PENDING', 'EDITED_PENDING', 'FLAGGED', 'REJECTED']:
        return Response({
            'error': 'Invalid operation',
            'message': f'Record is {record.status}, not reviewable'
        }, status=status.HTTP_400_BAD_REQUEST)

    actor = get_actor(request)
    with transaction.atomic():
        previous_value = record_audit_value(record)
        record.status = 'APPROVED'
        record.save()
        new_value = record_audit_value(record)

        AuditAction.objects.create(
            tenant=tenant,
            normalized_record=record,
            action='APPROVED',
            performed_by=actor,
            comment=request.data.get('comment', ''),
            previous_value=previous_value,
            new_value=new_value
        )

    return Response({
        'id': record.id,
        'status': record.status,
        'message': 'Record approved'
    })


@api_view(['PATCH'])
def reject_record(request, pk):
    """Flag a record for analyst follow-up."""
    tenant = get_tenant(request)
    comment = request.data.get('comment', '').strip()

    if not comment:
        return Response({
            'error': 'Comment required',
            'message': 'Flag reason must be provided'
        }, status=status.HTTP_400_BAD_REQUEST)

    try:
        record = NormalizedRecord.objects.get(id=pk, tenant=tenant, is_deleted=False)
    except NormalizedRecord.DoesNotExist:
        return Response({'error': 'Record not found'}, status=status.HTTP_404_NOT_FOUND)

    if record.status not in ['PENDING', 'EDITED_PENDING']:
        return Response({
            'error': 'Invalid operation',
            'message': f'Record is {record.status}, not pending review'
        }, status=status.HTTP_400_BAD_REQUEST)

    actor = get_actor(request)
    with transaction.atomic():
        previous_value = record_audit_value(record)
        record.status = 'FLAGGED'
        record.save()
        new_value = record_audit_value(record)

        AuditAction.objects.create(
            tenant=tenant,
            normalized_record=record,
            action='FLAGGED',
            performed_by=actor,
            comment=comment,
            previous_value=previous_value,
            new_value=new_value
        )

    return Response({
        'id': record.id,
        'status': record.status,
        'message': 'Record flagged'
    })


@api_view(['PATCH'])
def edit_record(request, pk):
    """Edit a record and reset to PENDING for re-review."""
    tenant = get_tenant(request)
    comment = request.data.get('comment', '').strip()

    if not comment:
        return Response({
            'error': 'Comment required',
            'message': 'Edit reason must be provided'
        }, status=status.HTTP_400_BAD_REQUEST)

    try:
        record = NormalizedRecord.objects.get(id=pk, tenant=tenant, is_deleted=False)
    except NormalizedRecord.DoesNotExist:
        return Response({'error': 'Record not found'}, status=status.HTTP_404_NOT_FOUND)

    if record.status == 'LOCKED':
        return Response({
            'error': 'Cannot edit locked record'
        }, status=status.HTTP_400_BAD_REQUEST)

    new_activity_value = request.data.get('activity_value')
    new_activity_unit = request.data.get('activity_unit')
    new_emission_factor = request.data.get('emission_factor')
    if new_activity_value is None or new_activity_unit is None or new_emission_factor is None:
        return Response({
            'error': 'activity_value, activity_unit, and emission_factor are required'
        }, status=status.HTTP_400_BAD_REQUEST)

    try:
        new_activity_value = Decimal(str(new_activity_value))
        new_emission_factor = Decimal(str(new_emission_factor))
    except (TypeError, InvalidOperation):
        return Response({
            'error': 'activity_value and emission_factor must be numbers'
        }, status=status.HTTP_400_BAD_REQUEST)

    new_activity_unit = str(new_activity_unit).strip()
    if not new_activity_unit:
        return Response({'error': 'activity_unit required'}, status=status.HTTP_400_BAD_REQUEST)

    actor = get_actor(request)
    with transaction.atomic():
        previous_value = record_audit_value(record)

        record.activity_value = new_activity_value
        record.activity_unit = new_activity_unit
        record.emission_factor = new_emission_factor
        record.co2e_kg = new_activity_value * new_emission_factor
        record.status = 'EDITED_PENDING'
        record.save()

        new_value = record_audit_value(record)

        AuditAction.objects.create(
            tenant=tenant,
            normalized_record=record,
            action='EDITED',
            performed_by=actor,
            comment=comment,
            previous_value=previous_value,
            new_value=new_value
        )

    return Response({
        'id': record.id,
        'status': record.status,
        'activity_value': float(record.activity_value) if record.activity_value is not None else None,
        'activity_unit': record.activity_unit,
        'emission_factor': float(record.emission_factor) if record.emission_factor is not None else None,
        'co2e_kg': float(record.co2e_kg) if record.co2e_kg is not None else None,
        'message': 'Record edited and marked for pending review'
    })


@api_view(['PATCH'])
def undo_record(request, pk):
    """Undo review status and return a record to PENDING."""
    tenant = get_tenant(request)
    try:
        record = NormalizedRecord.objects.get(id=pk, tenant=tenant, is_deleted=False)
    except NormalizedRecord.DoesNotExist:
        return Response({'error': 'Record not found'}, status=status.HTTP_404_NOT_FOUND)

    if record.status == 'PENDING':
        return Response({
            'error': 'Invalid operation',
            'message': 'Record is already PENDING'
        }, status=status.HTTP_400_BAD_REQUEST)

    if record.status == 'LOCKED':
        return Response({
            'error': 'Cannot undo locked record',
            'message': 'Locked records are terminal for audit. Create a correction run instead.'
        }, status=status.HTTP_400_BAD_REQUEST)

    previous_value = {
        'status': record.status,
        'locked_at': str(record.locked_at) if record.locked_at else None,
    }

    with transaction.atomic():
        record.status = 'PENDING'
        record.locked_at = None
        record.locked_by = None
        record.save()

        AuditAction.objects.create(
            tenant=tenant,
            normalized_record=record,
            action='UNDO',
            performed_by=get_actor(request),
            comment=request.data.get('comment', 'Undo review action'),
            previous_value=previous_value,
            new_value={'status': 'PENDING'}
        )

    return Response({
        'id': record.id,
        'status': record.status,
        'message': 'Record returned to PENDING'
    })


@api_view(['DELETE'])
def delete_record(request, pk):
    """Soft delete a normalized record from the review queue."""
    tenant = get_tenant(request)
    try:
        record = NormalizedRecord.objects.get(id=pk, tenant=tenant, is_deleted=False)
    except NormalizedRecord.DoesNotExist:
        return Response({'error': 'Record not found'}, status=status.HTTP_404_NOT_FOUND)

    if record.status == 'LOCKED':
        return Response({
            'error': 'Cannot delete locked record',
            'message': 'Locked records are terminal and cannot be deleted.'
        }, status=status.HTTP_400_BAD_REQUEST)

    actor = get_actor(request)

    with transaction.atomic():
        previous_value = record_audit_value(record)
        record.is_deleted = True
        record.deleted_at = timezone.now()
        record.deleted_by = actor
        record.save()
        new_value = record_audit_value(record)
        new_value.update({
            'deleted_at': str(record.deleted_at),
            'deleted_by': actor.username,
        })

        AuditAction.objects.create(
            tenant=tenant,
            normalized_record=record,
            action='DELETED',
            performed_by=actor,
            comment=request.data.get('comment', 'Soft deleted from review queue'),
            previous_value=previous_value,
            new_value=new_value
        )

    return Response({
        'id': record.id,
        'message': 'Record deleted'
    })


@api_view(['POST'])
def lock_records(request):
    """Lock all APPROVED records for audit (terminal)."""
    tenant = get_tenant(request)
    source_averages = get_source_averages(tenant)
    approved_records = NormalizedRecord.objects.filter(
        tenant=tenant,
        status='APPROVED',
        is_deleted=False
    )
    lockable_records = [
        record for record in approved_records
        if can_lock_record(record, source_averages)
    ]

    count = 0
    actor = get_actor(request)
    locked_at = timezone.now()
    with transaction.atomic():
        for record in lockable_records:
            previous_value = record_audit_value(record)
            record.status = 'LOCKED'
            record.locked_at = locked_at
            record.locked_by = actor
            record.save()
            new_value = record_audit_value(record)

            AuditAction.objects.create(
                tenant=tenant,
                normalized_record=record,
                action='LOCKED',
                performed_by=actor,
                previous_value=previous_value,
                new_value=new_value,
            )
            count += 1

    return Response({
        'locked_count': count,
        'skipped_count': approved_records.count() - count,
        'message': f'{count} records locked for audit'
    })


@api_view(['POST'])
def lock_record(request, pk):
    """Lock one approved record for audit (terminal)."""
    tenant = get_tenant(request)
    try:
        record = NormalizedRecord.objects.get(id=pk, tenant=tenant, is_deleted=False)
    except NormalizedRecord.DoesNotExist:
        return Response({'error': 'Record not found'}, status=status.HTTP_404_NOT_FOUND)

    if record.status != 'APPROVED':
        return Response({
            'error': 'Invalid operation',
            'message': 'Only approved records can be locked'
        }, status=status.HTTP_400_BAD_REQUEST)

    source_averages = get_source_averages(tenant)
    if not can_lock_record(record, source_averages):
        return Response({
            'error': 'Approval required',
            'message': 'Anomaly records must be explicitly approved by an analyst before locking.'
        }, status=status.HTTP_400_BAD_REQUEST)

    actor = get_actor(request)
    with transaction.atomic():
        previous_value = record_audit_value(record)
        record.status = 'LOCKED'
        record.locked_at = timezone.now()
        record.locked_by = actor
        record.save()
        new_value = record_audit_value(record)

        AuditAction.objects.create(
            tenant=tenant,
            normalized_record=record,
            action='LOCKED',
            performed_by=actor,
            previous_value=previous_value,
            new_value=new_value,
        )

    return Response({
        'id': record.id,
        'status': record.status,
        'locked_at': str(record.locked_at),
        'locked_by': actor.username,
        'message': 'Record locked'
    })


@api_view(['GET'])
def get_summary(request):
    """Get estimated, reportable, and locked Scope 1/2/3 totals."""
    tenant = get_tenant(request)
    ensure_demo_source_records(tenant)

    def totals_for(statuses):
        records = NormalizedRecord.objects.filter(
            tenant=tenant,
            is_deleted=False,
            status__in=statuses
        )
        totals = {'scope_1_co2e': 0.0, 'scope_2_co2e': 0.0, 'scope_3_co2e': 0.0, 'total_co2e': 0.0}
        for record in records:
            if record.co2e_kg is None:
                continue
            co2e = float(record.co2e_kg)
            if record.scope == 1:
                totals['scope_1_co2e'] += co2e
            elif record.scope == 2:
                totals['scope_2_co2e'] += co2e
            elif record.scope == 3:
                totals['scope_3_co2e'] += co2e
            totals['total_co2e'] += co2e
        return totals

    approved_totals = totals_for(['APPROVED'])
    estimated_totals = totals_for(['APPROVED', 'LOCKED'])
    summary = {
        'estimated': estimated_totals,
        'approved': approved_totals,
        'reportable': estimated_totals,
        'locked': totals_for(['LOCKED']),
        'by_status': {},
        'by_source_type': {},
    }
    summary.update(summary['reportable'])

    for status_choice in ['PENDING', 'EDITED_PENDING', 'APPROVED', 'FLAGGED', 'REJECTED', 'LOCKED']:
        count = NormalizedRecord.objects.filter(
            tenant=tenant,
            is_deleted=False,
            status=status_choice
        ).count()
        summary['by_status'][status_choice] = count

    summary['by_status']['FLAGGED'] += summary['by_status'].get('REJECTED', 0)

    for source_type in ['SAP', 'UTILITY', 'TRAVEL']:
        co2e_sum = NormalizedRecord.objects.filter(
            tenant=tenant,
            source_type=source_type,
            is_deleted=False,
            status__in=['APPROVED', 'LOCKED']
        ).aggregate(total=models.Sum('co2e_kg'))['total'] or 0.0
        summary['by_source_type'][source_type] = float(co2e_sum)

    summary['deleted_count'] = NormalizedRecord.objects.filter(
        tenant=tenant,
        is_deleted=True
    ).count()

    return Response(summary)


@api_view(['GET'])
def get_record_audit(request, pk):
    """Get a record with raw row and full audit trail."""
    tenant = get_tenant(request)
    try:
        record = NormalizedRecord.objects.select_related(
            'raw_record__ingestion_run', 'locked_by', 'deleted_by'
        ).get(id=pk, tenant=tenant)
    except NormalizedRecord.DoesNotExist:
        return Response({'error': 'Record not found'}, status=status.HTTP_404_NOT_FOUND)

    actions = AuditAction.objects.filter(
        tenant=tenant,
        normalized_record=record
    ).select_related('performed_by')

    edited_ids = set()
    if actions.filter(action='EDITED').exists():
        edited_ids.add(record.id)

    return Response({
        'record': serialize_record(record, edited_ids),
        'audit_actions': [serialize_audit_action(action) for action in actions],
    })


@api_view(['GET'])
def get_latest_failures(request):
    """Get failed/skipped rows from the latest ingestion run."""
    tenant = get_tenant(request)
    run = IngestionRun.objects.filter(tenant=tenant).order_by('-ingested_at').first()
    if not run:
        return Response({'run': None, 'failures': []})

    failed_records = RawRecord.objects.filter(
        ingestion_run=run,
        parse_error__isnull=False
    ).values('row_number', 'parse_error', 'raw_data')

    return Response({
        'run': {
            'run_id': str(run.id),
            'file_name': run.file_name,
            'status': run.status,
            'total_rows': run.total_rows,
            'parsed_rows': run.parsed_rows,
            'failed_rows': run.failed_rows,
            'skipped_rows': run.skipped_rows,
            'ingested_at': str(run.ingested_at),
        },
        'failures': list(failed_records),
    })


@api_view(['GET'])
def list_failed_records(request):
    """List failed raw rows across ingestion runs for the selected tenant."""
    tenant = get_tenant(request)
    ensure_demo_source_records(tenant)
    failures = RawRecord.objects.filter(
        tenant=tenant,
        parse_error__isnull=False
    ).select_related('ingestion_run').order_by('-created_at')[:500]
    return Response({
        'count': failures.count() if hasattr(failures, 'count') else len(failures),
        'results': [
            {
                'id': failure.id,
                'row_number': failure.row_number,
                'source_type': failure.source_type,
                'parse_error': failure.parse_error,
                'raw_data': failure.raw_data,
                'source_file_name': failure.ingestion_run.file_name,
                'ingested_at': str(failure.ingestion_run.ingested_at),
            }
            for failure in failures
        ]
    })


@api_view(['POST'])
def retry_failed_record(request, pk):
    """Prototype retry hook: records the retry attempt and returns the raw row."""
    tenant = get_tenant(request)
    try:
        failure = RawRecord.objects.select_related('ingestion_run').get(
            id=pk,
            tenant=tenant,
            parse_error__isnull=False
        )
    except RawRecord.DoesNotExist:
        return Response({'error': 'Failed row not found'}, status=status.HTTP_404_NOT_FOUND)

    return Response({
        'message': 'Retry queued for manual reprocessing',
        'failed_record': {
            'id': failure.id,
            'source_type': failure.source_type,
            'parse_error': failure.parse_error,
            'raw_data': failure.raw_data,
        }
    })


@api_view(['GET'])
def list_tenants(request):
    default_tenant = get_or_create_default_tenant()
    Tenant.objects.get_or_create(name='Acme Manufacturing')
    Tenant.objects.get_or_create(name='Globex Logistics')
    current = get_tenant(request) or default_tenant
    ensure_demo_source_records(current)
    with connection.cursor() as cursor:
        cursor.execute('SELECT id, name FROM ingestion_tenant ORDER BY name')
        tenant_rows = cursor.fetchall()
    tenants = []
    for tenant_id, name in tenant_rows:
        try:
            tenants.append({'id': str(uuid.UUID(str(tenant_id))), 'name': name})
        except ValueError:
            continue
    return Response({
        'current_tenant_id': str(current.id),
        'tenants': tenants
    })


@api_view(['GET'])
def get_run_details(request, run_id):
    """Get details of a specific ingestion run."""
    tenant = get_tenant(request)
    try:
        run = IngestionRun.objects.get(id=run_id, tenant=tenant)
    except IngestionRun.DoesNotExist:
        return Response({'error': 'Run not found'}, status=status.HTTP_404_NOT_FOUND)

    failed_records = RawRecord.objects.filter(
        ingestion_run=run,
        parse_error__isnull=False
    ).values('row_number', 'parse_error', 'raw_data')

    return Response({
        'run_id': str(run.id),
        'file_name': run.file_name,
        'status': run.status,
        'total_rows': run.total_rows,
        'parsed_rows': run.parsed_rows,
        'failed_rows': run.failed_rows,
        'skipped_rows': run.skipped_rows,
        'ingested_at': str(run.ingested_at),
        'failures': list(failed_records),
    })
