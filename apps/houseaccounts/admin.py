from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import HouseAccount, HouseAccountEntry, HouseAccountStatement


@admin.register(HouseAccount)
class HouseAccountAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["name", "company", "restaurant", "balance", "credit_limit", "status"]
    list_filter = ["status"]
    search_fields = ["name", "company", "phone"]


@admin.register(HouseAccountEntry)
class HouseAccountEntryAdmin(TenantAwareModelAdmin):
    tenant_field = "account__restaurant"
    list_display = ["account", "kind", "amount", "balance_after", "created_at"]


@admin.register(HouseAccountStatement)
class HouseAccountStatementAdmin(TenantAwareModelAdmin):
    tenant_field = "account__restaurant"
    list_display = ["account", "period_start", "period_end", "closing", "sent_at"]
