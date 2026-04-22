"""
Admin configuration for payments app with multi-tenant support.
"""

from django.contrib import admin, messages
from django.utils import timezone

from unfold.admin import ModelAdmin as UnfoldModelAdmin

from apps.core.admin import TenantAwareModelAdmin

from .models import (
    BogTransaction,
    FlittTransaction,
    Payment,
    PaymentMethod,
    Refund,
    RestaurantDebit,
)


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


@admin.register(FlittTransaction)
class FlittTransactionAdmin(UnfoldModelAdmin):
    """Read-only view of Flitt transactions — mirrors BogTransaction for audit symmetry."""

    list_display = [
        "flitt_order_id",
        "flow_type",
        "status",
        "settlement_status",
        "amount",
        "currency",
        "flitt_payment_id",
        "created_at",
    ]
    list_filter = ["flow_type", "status", "settlement_status", "currency", "created_at"]
    search_fields = [
        "flitt_order_id",
        "flitt_payment_id",
        "order__order_number",
        "reservation__confirmation_code",
    ]
    readonly_fields = [
        "id",
        "flitt_order_id",
        "flitt_payment_id",
        "flow_type",
        "order",
        "reservation",
        "session",
        "initiated_by",
        "amount",
        "currency",
        "status",
        "settlement_status",
        "settlement_id",
        "settlement_error",
        "settled_at",
        "split_snapshot",
        "checkout_url",
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
    raw_id_fields = ["order", "reservation", "session", "initiated_by"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):  # noqa: ARG002
        return False

    def has_delete_permission(self, request, obj=None):  # noqa: ARG002
        return False


@admin.register(RestaurantDebit)
class RestaurantDebitAdmin(UnfoldModelAdmin):
    """
    Platform ledger of amounts owed by restaurants — mostly BOG refund
    claw-backs. Platform accounting settles these off-platform (invoice /
    next payout netting) and marks them settled via the bulk action below.
    """

    list_display = [
        "restaurant",
        "amount",
        "currency",
        "source",
        "status",
        "settled_at",
        "created_at",
    ]
    list_filter = ["source", "status", "currency", "created_at"]
    search_fields = [
        "restaurant__name",
        "source_bog_transaction__bog_order_id",
        "notes",
    ]
    readonly_fields = [
        "id",
        "restaurant",
        "amount",
        "currency",
        "source",
        "source_bog_transaction",
        "source_refund",
        "settled_at",
        "settled_by",
        "created_at",
        "updated_at",
    ]
    fields = [
        "id",
        "restaurant",
        "amount",
        "currency",
        "source",
        "source_bog_transaction",
        "source_refund",
        "status",
        "settled_at",
        "settled_by",
        "notes",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "created_at"
    actions = ["mark_settled", "mark_written_off"]

    @admin.action(description="Mark selected debits as settled (off-platform)")
    def mark_settled(self, request, queryset):
        n = 0
        for debit in queryset.filter(status=RestaurantDebit.STATUS_OUTSTANDING):
            debit.status = RestaurantDebit.STATUS_SETTLED
            debit.settled_at = timezone.now()
            debit.settled_by = request.user
            debit.save(update_fields=["status", "settled_at", "settled_by", "updated_at"])
            n += 1
        self.message_user(request, f"Marked {n} debit(s) as settled.", level=messages.SUCCESS)

    @admin.action(description="Write off selected debits (uncollectable)")
    def mark_written_off(self, request, queryset):
        n = queryset.filter(status=RestaurantDebit.STATUS_OUTSTANDING).update(
            status=RestaurantDebit.STATUS_WRITTEN_OFF,
            settled_at=timezone.now(),
            settled_by=request.user,
        )
        self.message_user(request, f"Wrote off {n} debit(s).", level=messages.SUCCESS)


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
