from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import Printer, PrintJob


@admin.register(Printer)
class PrinterAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["name", "restaurant", "kind", "stations", "paper", "connection", "is_active", "last_seen_at"]
    list_filter = ["kind", "connection", "is_active"]
    search_fields = ["name", "restaurant__name"]
    readonly_fields = ["bridge_key", "last_seen_at", "last_error", "created_at", "updated_at"]
    raw_id_fields = ["restaurant"]


@admin.register(PrintJob)
class PrintJobAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["title", "restaurant", "printer", "kind", "status", "attempts", "created_at", "printed_at"]
    list_filter = ["kind", "status"]
    search_fields = ["title", "order__order_number"]
    readonly_fields = [f.name for f in PrintJob._meta.fields if f.name != "id"]
    raw_id_fields = ["restaurant", "printer", "order", "payment", "requested_by"]
