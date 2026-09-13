from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import PaymentTerminal, TerminalTransaction


@admin.register(PaymentTerminal)
class PaymentTerminalAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["name", "restaurant", "provider", "is_active", "is_default", "last_seen_at"]
    list_filter = ["provider", "is_active"]


@admin.register(TerminalTransaction)
class TerminalTransactionAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["id", "restaurant", "terminal", "kind", "amount", "status", "created_at"]
    list_filter = ["status", "kind"]
    search_fields = ["external_id", "order__order_number"]
