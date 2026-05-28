from django.conf import settings
from django.contrib.auth.models import User
from .tenancy import ensure_user_tenant, get_or_create_default_tenant, get_tenant_by_id


class TenantMiddleware:
    """Attach request.tenant using UserProfile, with a local demo fallback."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = self._resolve_tenant(request)
        return self.get_response(request)

    def _resolve_tenant(self, request):
        tenant_id = request.headers.get('X-Tenant-ID') or request.GET.get('tenant_id')
        if tenant_id:
            tenant = get_tenant_by_id(tenant_id)
            if tenant is not None:
                return tenant

        if request.user.is_authenticated:
            tenant = ensure_user_tenant(request.user)
            if tenant is not None:
                return tenant
            return None

        if settings.DEBUG:
            tenant = get_or_create_default_tenant()
            user, _ = User.objects.get_or_create(username='demo-analyst')
            return ensure_user_tenant(user) or tenant

        return None
