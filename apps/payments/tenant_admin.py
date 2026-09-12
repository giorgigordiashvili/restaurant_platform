"""
Cash & payments in the tenant admin: shifts with their Z report, the
payment ledger (read-only, with allocations and refunds) and the reasons
staff pick for discounts / comps / voids / refunds.
"""

from __future__ import annotations

from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from unfold.admin import TabularInline as UnfoldTabularInline
from unfold.decorators import display

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantInlineMixin, TenantModelAdmin
from apps.payments.models import CashMovement, CashShift, DiscountReason, Payment, PaymentAllocation, Refund

METHOD_LABELS = dict(Payment.PAYMENT_METHOD_CHOICES)


class CashEnabledMixin(ModuleEnabledMixin):
    module_code = "cash"


class CashMovementInline(TenantInlineMixin, UnfoldTabularInline):
    permission_resource = "cash"
    permission_actions = {"view": "read", "add": "read", "change": "read", "delete": "read"}
    model = CashMovement
    extra = 0
    can_delete = False
    fields = ["kind", "amount", "reason", "created_by", "created_at"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class ShiftPaymentInline(TenantInlineMixin, UnfoldTabularInline):
    permission_resource = "cash"
    permission_actions = {"view": "read", "add": "read", "change": "read", "delete": "read"}
    model = Payment
    fk_name = "shift"
    extra = 0
    can_delete = False
    fields = [
        "receipt_number",
        "payment_method",
        "amount",
        "tip_amount",
        "change_given",
        "status",
        "order",
        "completed_at",
    ]
    readonly_fields = fields
    ordering = ["-created_at"]

    def has_add_permission(self, request, obj=None):
        return False


class CashShiftTenantAdmin(CashEnabledMixin, TenantModelAdmin):
    """Shifts are opened and closed in the POS; here they are the Z-report archive."""

    permission_resource = "cash"
    list_display = ["number", "status", "opened_at", "opened_by", "closed_at", "expected_cash", "counted_cash", "diff"]
    list_filter = ["status", "opened_at"]
    ordering = ["-opened_at"]
    date_hierarchy = "opened_at"
    inlines = [CashMovementInline, ShiftPaymentInline]
    readonly_fields = [
        "number",
        "register",
        "status",
        "opened_by",
        "opened_at",
        "closed_by",
        "closed_at",
        "opening_float",
        "counted_cash",
        "expected_cash",
        "difference",
        "notes",
        "z_report",
    ]
    fieldsets = (
        (None, {"fields": ("number", "register", "status", "opened_by", "opened_at", "closed_by", "closed_at")}),
        ("Cash", {"fields": ("opening_float", "expected_cash", "counted_cash", "difference", "notes")}),
        ("Z report", {"fields": ("z_report",)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("opened_by", "closed_by")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description=_("Difference"))
    def diff(self, obj):
        if obj.difference is None:
            return "—"
        colour = (
            "text-red-600" if obj.difference < 0 else ("text-amber-600" if obj.difference > 0 else "text-green-600")
        )
        return format_html('<span class="{}">{:+.2f}</span>', colour, obj.difference)

    @display(description=_("Z report"))
    def z_report(self, obj):
        from apps.payments import services

        report = obj.report if obj.status == "closed" and obj.report else services.x_report(obj)
        rows = [
            ("Sales", report.get("sales")),
            ("Tips", report.get("tips")),
            ("Refunds", report.get("refunds")),
            ("Net sales", report.get("net_sales")),
            ("Payments", report.get("payments_count")),
            ("Orders", report.get("orders_count")),
            ("Opening float", report.get("opening_float")),
            ("Cash sales", report.get("cash_sales")),
            ("Cash tips", report.get("cash_tips")),
            ("Paid in", report.get("paid_in")),
            ("Paid out", report.get("paid_out")),
            ("Cash refunds", report.get("cash_refunds")),
            ("Expected cash", report.get("expected_cash")),
            ("Counted cash", report.get("counted_cash", "—")),
            ("Difference", report.get("difference", "—")),
        ]
        method_rows = [
            (METHOD_LABELS.get(m, m), f"{v['count']} × {v['amount']} (+ tips {v['tips']})")
            for m, v in (report.get("by_method") or {}).items()
        ]
        d = report.get("discounts") or {}
        c = report.get("comps") or {}
        v = report.get("voids") or {}
        extra_rows = [
            ("Order discounts", f"{d.get('orders_count', 0)} × {d.get('orders_amount', '0')}"),
            ("Item discounts", f"{d.get('items_count', 0)} × {d.get('items_amount', '0')}"),
            ("Comps", f"{c.get('count', 0)} × {c.get('amount', '0')}"),
            (
                "Voids",
                f"{v.get('count', 0)} × {v.get('amount', '0')} (after kitchen: {v.get('after_kitchen_count', 0)})",
            ),
        ]

        def table(title, items):
            body = format_html_join(
                "",
                '<tr><td class="pr-6 py-0.5 text-base-500">{}</td><td class="py-0.5 font-medium">{}</td></tr>',
                items,
            )
            return format_html(
                '<h4 class="font-semibold mt-3 mb-1">{}</h4><table class="text-sm">{}</table>', title, body
            )

        title = "Z report" if obj.status == "closed" else "X report (live)"
        return format_html(
            '<div data-testid="z-report">{}{}{}</div>',
            table(title, rows),
            table("By method", method_rows or [("—", "no payments")]),
            table("Discounts & voids", extra_rows),
        )


class PaymentAllocationInline(TenantInlineMixin, UnfoldTabularInline):
    permission_resource = "cash"
    permission_actions = {"view": "read", "add": "read", "change": "read", "delete": "read"}
    model = PaymentAllocation
    extra = 0
    can_delete = False
    fields = ["order", "amount"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class RefundInline(TenantInlineMixin, UnfoldTabularInline):
    permission_resource = "cash"
    permission_actions = {"view": "read", "add": "read", "change": "read", "delete": "read"}
    model = Refund
    fk_name = "payment"
    extra = 0
    can_delete = False
    fields = ["amount", "method", "reason", "reason_details", "status", "processed_by", "completed_at"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class PaymentTenantAdmin(CashEnabledMixin, TenantModelAdmin):
    """The payment ledger, read-only. Money moves in the POS."""

    permission_resource = "cash"
    list_display = [
        "receipt_number",
        "payment_method",
        "amount",
        "tip_amount",
        "status",
        "order",
        "shift",
        "processed_by",
        "completed_at",
    ]
    list_filter = ["payment_method", "status", "completed_at"]
    search_fields = ["receipt_number", "external_payment_id", "order__order_number"]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"
    inlines = [PaymentAllocationInline, RefundInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("order", "shift", "processed_by")

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in Payment._meta.fields if f.name != "id"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DiscountReasonTenantAdmin(CashEnabledMixin, TenantModelAdmin):
    permission_resource = "cash"
    list_display = ["label", "kind", "requires_manager", "is_active", "sort_order"]
    list_filter = ["kind", "is_active", "requires_manager"]
    list_editable = ["is_active", "sort_order"]
    search_fields = ["label"]
    fields = ["kind", "label", "requires_manager", "is_active", "sort_order"]
    ordering = ["kind", "sort_order", "label"]


def register_payments_admin(site):
    site.register(CashShift, CashShiftTenantAdmin)
    site.register(Payment, PaymentTenantAdmin)
    site.register(DiscountReason, DiscountReasonTenantAdmin)
