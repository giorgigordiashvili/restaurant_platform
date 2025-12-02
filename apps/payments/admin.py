"""
Admin configuration for payments app.
"""

from django.contrib import admin

from .models import Payment, PaymentMethod, Refund


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "order",
        "amount",
        "tip_amount",
        "total_amount",
        "payment_method",
        "status",
        "receipt_number",
        "created_at",
    ]
    list_filter = ["status", "payment_method", "created_at"]
    search_fields = [
        "receipt_number",
        "external_payment_id",
        "payment_intent_id",
        "order__order_number",
    ]
    readonly_fields = [
        "id",
        "total_amount",
        "receipt_number",
        "created_at",
        "updated_at",
        "completed_at",
        "failed_at",
    ]
    raw_id_fields = ["order", "customer", "processed_by"]
    date_hierarchy = "created_at"


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "payment",
        "amount",
        "reason",
        "status",
        "created_at",
    ]
    list_filter = ["status", "reason", "created_at"]
    search_fields = ["external_refund_id", "payment__receipt_number"]
    readonly_fields = ["id", "created_at", "updated_at", "completed_at", "failed_at"]
    raw_id_fields = ["payment", "processed_by"]


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "customer",
        "method_type",
        "card_brand",
        "card_last4",
        "is_default",
        "is_active",
        "created_at",
    ]
    list_filter = ["method_type", "is_default", "is_active", "card_brand"]
    search_fields = ["customer__email", "external_method_id", "card_last4"]
    readonly_fields = ["id", "created_at", "updated_at"]
    raw_id_fields = ["customer"]
