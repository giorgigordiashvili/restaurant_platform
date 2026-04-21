from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import (
    LoyaltyCounter,
    LoyaltyProgram,
    LoyaltyRedemption,
    PlatformLoyaltyLedger,
    PlatformLoyaltyTier,
)


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


# ─── Platform-wide tiered loyalty ────────────────────────────────────────────


@admin.register(PlatformLoyaltyTier)
class PlatformLoyaltyTierAdmin(admin.ModelAdmin):
    list_display = [
        "slug",
        "name_en",
        "min_points",
        "discount_percent",
        "display_order",
        "is_active",
    ]
    list_filter = ["is_active"]
    search_fields = ["slug", "name_en", "name_ka", "name_ru"]
    ordering = ["min_points"]
    fields = [
        "slug",
        "display_order",
        ("name_ka", "name_en", "name_ru"),
        "min_points",
        "discount_percent",
        "is_active",
    ]


@admin.register(PlatformLoyaltyLedger)
class PlatformLoyaltyLedgerAdmin(admin.ModelAdmin):
    list_display = ["earned_at", "user", "restaurant", "order", "points", "source"]
    list_filter = ["source", "restaurant"]
    search_fields = ["user__user__email", "order__order_number"]
    readonly_fields = ["user", "order", "restaurant", "points", "earned_at", "source"]
    date_hierarchy = "earned_at"
    ordering = ["-earned_at"]

    def has_add_permission(self, request):
        # Ledger entries are written only by the accrual signal; never
        # by hand. Read-only audit surface.
        return False
