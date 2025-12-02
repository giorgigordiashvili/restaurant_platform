"""
Admin configuration for orders app.
"""

from django.contrib import admin

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
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "order_number",
        "restaurant",
        "table",
        "order_type",
        "status",
        "total",
        "created_at",
    ]
    list_filter = ["restaurant", "order_type", "status", "created_at"]
    search_fields = ["order_number", "customer_name", "customer_phone"]
    readonly_fields = ["order_number", "subtotal", "tax_amount", "service_charge", "total"]
    date_hierarchy = "created_at"
    inlines = [OrderItemInline, OrderStatusHistoryInline]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ["order", "item_name", "quantity", "unit_price", "total_price", "status"]
    list_filter = ["order__restaurant", "status", "preparation_station"]
    search_fields = ["item_name", "order__order_number"]


@admin.register(OrderItemModifier)
class OrderItemModifierAdmin(admin.ModelAdmin):
    list_display = ["order_item", "modifier_name", "price_adjustment"]
    search_fields = ["modifier_name", "order_item__item_name"]


@admin.register(OrderStatusHistory)
class OrderStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ["order", "from_status", "to_status", "changed_by", "created_at"]
    list_filter = ["to_status", "created_at"]
    search_fields = ["order__order_number"]
    readonly_fields = ["order", "from_status", "to_status", "changed_by", "created_at"]
