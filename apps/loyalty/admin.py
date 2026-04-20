from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import LoyaltyCounter, LoyaltyProgram, LoyaltyRedemption


@admin.register(LoyaltyProgram)
class LoyaltyProgramAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = [
        "name",
        "restaurant",
        "trigger_item",
        "threshold",
        "reward_item",
        "reward_quantity",
        "is_active",
        "starts_at",
        "ends_at",
    ]
    list_filter = ["is_active", "restaurant"]
    search_fields = ["name"]
    autocomplete_fields = ["restaurant", "trigger_item", "reward_item"]


@admin.register(LoyaltyCounter)
class LoyaltyCounterAdmin(TenantAwareModelAdmin):
    tenant_field = "program__restaurant"
    list_display = ["program", "user", "phone_number", "punches", "last_earned_at"]
    list_filter = ["program__restaurant", "program"]
    search_fields = ["user__email", "phone_number"]
    autocomplete_fields = ["program", "user"]


@admin.register(LoyaltyRedemption)
class LoyaltyRedemptionAdmin(TenantAwareModelAdmin):
    tenant_field = "program__restaurant"
    list_display = ["code", "program", "user", "status", "issued_at", "expires_at", "redeemed_at"]
    list_filter = ["status", "program__restaurant"]
    search_fields = ["code", "user__email", "phone_number"]
    readonly_fields = ["code", "issued_at", "expires_at", "redeemed_at", "redeemed_by"]
    autocomplete_fields = ["program", "counter", "user", "redeemed_order"]
