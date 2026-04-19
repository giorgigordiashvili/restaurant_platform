"""
Admin configuration for payments app with multi-tenant support.
"""

from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from apps.core.admin import TenantAwareModelAdmin

from .models import BogTransaction, Payment, PaymentMethod, Refund


@admin.register(Payment)
class PaymentAdmin(TenantAwareModelAdmin):
    """Admin for payments with tenant filtering."""

    tenant_field = "order__restaurant"

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
class RefundAdmin(TenantAwareModelAdmin):
    """Admin for refunds with tenant filtering."""

    tenant_field = "payment__order__restaurant"

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


@admin.register(BogTransaction)
class BogTransactionAdmin(UnfoldModelAdmin):
    """Read-only view of BOG Payment Manager transactions for debugging."""

    list_display = [
        "bog_order_id",
        "flow_type",
        "status",
        "amount",
        "currency",
        "code",
        "external_order_id",
        "created_at",
    ]
    list_filter = ["flow_type", "status", "currency", "created_at"]
    search_fields = [
        "bog_order_id",
        "external_order_id",
        "order__order_number",
        "reservation__confirmation_code",
    ]
    readonly_fields = [
        "id",
        "bog_order_id",
        "external_order_id",
        "flow_type",
        "order",
        "reservation",
        "payment_method",
        "initiated_by",
        "amount",
        "currency",
        "status",
        "code",
        "code_description",
        "reject_reason",
        "redirect_url",
        "return_url",
        "callback_url",
        "request_payload",
        "response_payload",
        "last_webhook_payload",
        "last_webhook_at",
        "last_reconciled_at",
        "created_at",
        "updated_at",
    ]
    raw_id_fields = ["order", "reservation", "payment_method", "initiated_by"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None):  # noqa: ARG002
        return False


@admin.register(PaymentMethod)
class PaymentMethodAdmin(UnfoldModelAdmin):
    """
    Admin for payment methods.
    No tenant filtering - payment methods belong to users, not restaurants.
    """

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
