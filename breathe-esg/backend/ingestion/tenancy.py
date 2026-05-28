import uuid

from django.db import connection

from .models import Tenant, UserProfile


def parse_tenant_id(value):
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def get_tenant_by_id(value):
    tenant_id = parse_tenant_id(value)
    if tenant_id is None:
        return None
    try:
        return Tenant.objects.filter(id=tenant_id).first()
    except (TypeError, ValueError):
        return None


def get_first_available_tenant():
    with connection.cursor() as cursor:
        cursor.execute('SELECT id FROM ingestion_tenant ORDER BY created_at DESC')
        tenant_ids = [row[0] for row in cursor.fetchall()]

    for tenant_id in tenant_ids:
        tenant = get_tenant_by_id(tenant_id)
        if tenant is not None:
            return tenant

    return Tenant.objects.create(name='Default Tenant')


def get_or_create_default_tenant():
    tenant = None
    try:
        tenant = Tenant.objects.filter(name='Default Tenant').first()
    except (TypeError, ValueError):
        tenant = None
    return tenant or get_first_available_tenant()


def get_user_profile(user):
    if not user or not user.is_authenticated:
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT id, tenant_id FROM ingestion_userprofile WHERE user_id = %s LIMIT 1',
            [user.id],
        )
        row = cursor.fetchone()
    if not row or get_tenant_by_id(row[1]) is None:
        return None
    return UserProfile.objects.filter(id=row[0]).first()


def ensure_user_tenant(user):
    if not user or not user.is_authenticated:
        return None

    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT id, tenant_id FROM ingestion_userprofile WHERE user_id = %s LIMIT 1',
            [user.id],
        )
        profile_row = cursor.fetchone()

    if profile_row:
        profile_id, tenant_id = profile_row
        tenant = get_tenant_by_id(tenant_id)
        if tenant is not None:
            return tenant

        tenant = get_first_available_tenant()
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE ingestion_userprofile SET tenant_id = %s WHERE id = %s',
                [tenant.id.hex, profile_id],
            )
        return tenant

    tenant = get_first_available_tenant()
    UserProfile.objects.create(user=user, tenant=tenant)
    return tenant


def resolve_request_tenant(request):
    tenant = get_tenant_by_id(
        request.headers.get('X-Tenant-ID') or request.GET.get('tenant_id')
    )
    if tenant is not None:
        return tenant

    tenant = ensure_user_tenant(getattr(request, 'user', None))
    if tenant is not None:
        return tenant

    return get_or_create_default_tenant()
