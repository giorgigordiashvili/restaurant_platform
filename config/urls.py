"""
URL configuration for restaurant_platform project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.core.views import health_check, readiness_check

urlpatterns = [
    # Health checks (for load balancers and monitoring)
    path("api/v1/health/", health_check, name="health_check"),
    path("api/v1/ready/", readiness_check, name="readiness_check"),
    # Admin
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
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
