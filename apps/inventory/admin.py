"""Superadmin views of the warehouse tables (tenant-filtered like everything else)."""

from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import (
    EmployeeMeal,
    InventoryAlert,
    RecipeLine,
    RestaurantDeliveryPlatform,
    StockAdjustment,
    StockItem,
    StockLot,
    StockMovement,
    UnitOfMeasure,
    WasteEntry,
)


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "dimension", "factor_to_base", "display_order"]
    list_filter = ["dimension"]


@admin.register(StockItem)
class StockItemAdmin(TenantAwareModelAdmin):
    list_display = [
        "name",
        "restaurant",
        "base_unit",
        "on_hand_qty",
        "reserved_qty",
        "min_level",
        "par_level",
        "is_active",
    ]
    list_filter = ["is_active"]
    search_fields = ["name", "sku", "restaurant__name"]


@admin.register(StockLot)
class StockLotAdmin(TenantAwareModelAdmin):
    list_display = [
        "stock_item",
        "restaurant",
        "received_qty",
        "remaining_qty",
        "unit_cost",
        "expiry_date",
        "received_at",
    ]
    list_filter = ["is_expired_out"]
    search_fields = ["stock_item__name", "reference"]


@admin.register(StockMovement)
class StockMovementAdmin(TenantAwareModelAdmin):
    list_display = ["created_at", "restaurant", "stock_item", "kind", "reason", "quantity", "total_cost", "order"]
    list_filter = ["kind", "reason"]
    search_fields = ["stock_item__name", "order__order_number"]


@admin.register(RecipeLine)
class RecipeLineAdmin(TenantAwareModelAdmin):
    tenant_field = "stock_item__restaurant"
    list_display = ["menu_item", "modifier", "stock_item", "quantity", "unit"]
    search_fields = ["stock_item__name"]


@admin.register(WasteEntry)
class WasteEntryAdmin(TenantAwareModelAdmin):
    list_display = ["occurred_on", "restaurant", "stock_item", "quantity", "unit", "reason", "total_cost"]
    list_filter = ["reason"]


@admin.register(EmployeeMeal)
class EmployeeMealAdmin(TenantAwareModelAdmin):
    list_display = ["meal_date", "restaurant", "staff_member", "menu_item", "stock_item", "quantity", "total_cost"]


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(TenantAwareModelAdmin):
    list_display = ["created_at", "restaurant", "stock_item", "mode", "quantity", "unit", "delta_base"]


@admin.register(InventoryAlert)
class InventoryAlertAdmin(TenantAwareModelAdmin):
    list_display = ["created_at", "restaurant", "kind", "status", "message"]
    list_filter = ["kind", "status"]


@admin.register(RestaurantDeliveryPlatform)
class RestaurantDeliveryPlatformAdmin(TenantAwareModelAdmin):
    list_display = ["restaurant", "platform", "is_enabled", "store_external_id"]
    list_filter = ["platform", "is_enabled"]
