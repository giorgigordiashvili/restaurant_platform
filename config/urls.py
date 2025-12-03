"""
URL configuration for restaurant_platform project.
"""

from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.core.admin_sites import tenant_admin_site
from apps.core.admin_views import set_simulated_restaurant
from apps.core.views import health_check, readiness_check


def get_admin_urls(request):
    """
    Return the appropriate admin URLs based on request context.

    - If request.is_tenant_admin is True (restaurant subdomain), use tenant_admin_site
    - Otherwise, use the default Django admin site
    """
    if getattr(request, "is_tenant_admin", False):
        return tenant_admin_site.urls
    return admin.site.urls


# Custom admin URL pattern that dynamically routes to the correct admin site
class DynamicAdminURLPattern:
    """
    URL pattern that dynamically routes to the correct admin site.

    Uses the is_tenant_admin flag set by TenantMiddleware to determine
    whether to use the tenant admin (unfold) or default Django admin.
    """

    def __init__(self):
        self.pattern = ""

    def resolve(self, path):
        # This will be called by Django's URL resolver
        # We intercept at the view level instead
        return None


urlpatterns = [
    # Health checks (for load balancers and monitoring)
    path("api/v1/health/", health_check, name="health_check"),
    path("api/v1/ready/", readiness_check, name="readiness_check"),
    # Language switching
    path("i18n/", include("django.conf.urls.i18n")),
    # Admin - tenant simulation URL must come before admin.site.urls
    # (Only used by superadmin on main domain)
    path("admin/simulate-restaurant/", set_simulated_restaurant, name="admin-simulate-restaurant"),
    # Tenant admin (unfold-based) for restaurant subdomains
    path("tenant-admin/", tenant_admin_site.urls),
    # Default Django admin for superadmins
    path("admin/", admin.site.urls),
    # API Documentation
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # API v1
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/users/", include("apps.accounts.urls_users")),
    path("api/v1/restaurants/", include("apps.tenants.urls")),
    path("api/v1/menu/", include("apps.menu.urls")),
    path("api/v1/tables/", include("apps.tables.urls")),
    path("api/v1/orders/", include("apps.orders.urls")),
    path("api/v1/reservations/", include("apps.reservations.urls")),
    path("api/v1/payments/", include("apps.payments.urls")),
    path("api/v1/favorites/", include("apps.favorites.urls")),
    # Staff (public)
    path("api/v1/staff/", include("apps.staff.urls")),
    # Dashboard API (tenant-scoped)
    path("api/v1/dashboard/settings/", include("apps.tenants.urls_dashboard")),
    path("api/v1/dashboard/staff/", include("apps.staff.urls_dashboard")),
    path("api/v1/dashboard/menu/", include("apps.menu.urls_dashboard")),
    path("api/v1/dashboard/tables/", include("apps.tables.urls_dashboard")),
    path("api/v1/dashboard/orders/", include("apps.orders.urls_dashboard")),
    path("api/v1/dashboard/payments/", include("apps.payments.urls_dashboard")),
    path("api/v1/dashboard/reservations/", include("apps.reservations.urls_dashboard")),
    path("api/v1/dashboard/audit/", include("apps.audit.urls_dashboard")),
    # Admin API (platform-wide, staff only)
    path("api/v1/admin/audit/", include("apps.audit.urls")),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
