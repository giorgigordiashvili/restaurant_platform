"""
URL configuration for restaurant_platform project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

urlpatterns = [
    # Admin
    path('admin/', admin.site.urls),

    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # API v1
    path('api/v1/auth/', include('apps.accounts.urls')),
    path('api/v1/users/', include('apps.accounts.urls_users')),
    path('api/v1/restaurants/', include('apps.tenants.urls')),
    path('api/v1/menu/', include('apps.menu.urls')),
    path('api/v1/tables/', include('apps.tables.urls')),
    path('api/v1/orders/', include('apps.orders.urls')),
    path('api/v1/reservations/', include('apps.reservations.urls')),
    path('api/v1/payments/', include('apps.payments.urls')),
    path('api/v1/favorites/', include('apps.favorites.urls')),

    # Dashboard API (tenant-scoped)
    path('api/v1/dashboard/', include('apps.tenants.urls_dashboard')),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
