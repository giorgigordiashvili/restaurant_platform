from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import GiftCard, GiftCardTransaction


@admin.register(GiftCard)
class GiftCardAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["code", "restaurant", "initial_value", "balance", "status", "kind", "created_at"]
    list_filter = ["status", "kind"]
    search_fields = ["code", "recipient_name", "purchaser_name"]


@admin.register(GiftCardTransaction)
class GiftCardTransactionAdmin(TenantAwareModelAdmin):
    tenant_field = "card__restaurant"
    list_display = ["card", "kind", "amount", "balance_after", "created_at"]
