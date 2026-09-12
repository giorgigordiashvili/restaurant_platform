from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import DeliveryPlatformEvent, MenuImport, PlatformMenuSync


@admin.register(DeliveryPlatformEvent)
class DeliveryPlatformEventAdmin(TenantAwareModelAdmin):
    tenant_field = "link__restaurant"
    list_display = ["event_id", "kind", "link", "order", "processed_at", "error", "created_at"]
    list_filter = ["kind", "link__platform"]
    search_fields = ["event_id", "order__order_number"]
    readonly_fields = [f.name for f in DeliveryPlatformEvent._meta.fields if f.name != "id"]


@admin.register(PlatformMenuSync)
class PlatformMenuSyncAdmin(TenantAwareModelAdmin):
    tenant_field = "link__restaurant"
    list_display = ["link", "status", "product_count", "transaction_id", "started_at", "finished_at", "error"]
    list_filter = ["status", "link__platform"]
    readonly_fields = [f.name for f in PlatformMenuSync._meta.fields if f.name != "id"]


@admin.register(MenuImport)
class MenuImportAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["restaurant", "source", "status", "applied_at", "error", "created_at"]
    list_filter = ["status", "source"]
    readonly_fields = [f.name for f in MenuImport._meta.fields if f.name != "id"]
