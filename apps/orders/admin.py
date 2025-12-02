"""
Admin configuration for orders app with multi-tenant support.
"""

from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import Order, OrderItem, OrderItemModifier, OrderStatusHistory


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ["total_price"]


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    readonly_fields = ["from_status", "to_status", "changed_by", "created_at"]


@admin.register(Order)
class OrderAdmin(TenantAwareModelAdmin):
    """Admin for orders with tenant filtering."""

    tenant_field = "restaurant"

    list_display = [
        "order_number",
        "restaurant",
        "table",
        "order_type",
        "status",
        "total",
        "created_at",
    ]
    list_filter = ["order_type", "status", "created_at"]
    search_fields = ["order_number", "customer_name", "customer_phone"]
    readonly_fields = ["order_number", "subtotal", "tax_amount", "service_charge", "total"]
    date_hierarchy = "created_at"
    inlines = [OrderItemInline, OrderStatusHistoryInline]

    actions = ["export_as_csv", "export_as_json", "mark_completed"]

    @admin.action(description="Mark selected orders as completed")
    def mark_completed(self, request, queryset):
        updated = queryset.filter(status__in=["pending", "confirmed", "preparing", "ready"]).update(
            status="completed"
        )
        self.message_user(request, f"{updated} order(s) marked as completed.")


@admin.register(OrderItem)
class OrderItemAdmin(TenantAwareModelAdmin):
    """Admin for order items with tenant filtering."""

    tenant_field = "order__restaurant"

    list_display = ["order", "item_name", "quantity", "unit_price", "total_price", "status"]
    list_filter = ["status", "preparation_station"]
    search_fields = ["item_name", "order__order_number"]


@admin.register(OrderItemModifier)
class OrderItemModifierAdmin(TenantAwareModelAdmin):
    """Admin for order item modifiers with tenant filtering."""

    tenant_field = "order_item__order__restaurant"

    list_display = ["order_item", "modifier_name", "price_adjustment"]
    search_fields = ["modifier_name", "order_item__item_name"]


@admin.register(OrderStatusHistory)
class OrderStatusHistoryAdmin(TenantAwareModelAdmin):
    """Admin for order status history with tenant filtering."""

    tenant_field = "order__restaurant"

    list_display = ["order", "from_status", "to_status", "changed_by", "created_at"]
    list_filter = ["to_status", "created_at"]
    search_fields = ["order__order_number"]
    readonly_fields = ["order", "from_status", "to_status", "changed_by", "created_at"]
