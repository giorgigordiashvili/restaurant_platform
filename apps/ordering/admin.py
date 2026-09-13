from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import Courier, Delivery, DeliveryZone, OnlineOrderingSettings, RestaurantDomain


@admin.register(OnlineOrderingSettings)
class OnlineOrderingSettingsAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["restaurant", "pickup_enabled", "delivery_enabled", "courier_provider", "paused_until"]


@admin.register(DeliveryZone)
class DeliveryZoneAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["name", "restaurant", "kind", "radius_km", "fee", "min_order", "is_active"]


@admin.register(Courier)
class CourierAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["name", "restaurant", "phone", "is_active", "is_available"]


@admin.register(Delivery)
class DeliveryAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["order", "restaurant", "provider", "status", "courier_name", "cost", "created_at"]
    list_filter = ["provider", "status"]
    search_fields = ["order__order_number", "external_id"]


@admin.register(RestaurantDomain)
class RestaurantDomainAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["domain", "restaurant", "is_primary", "verified_at", "error"]
