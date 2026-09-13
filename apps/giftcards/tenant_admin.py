"""Tenant admin: gift cards (list with Void / Resend / Adjust), and a 'sell a physical batch' page."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from unfold.admin import TabularInline
from unfold.decorators import action, display

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantInlineMixin, TenantModelAdmin, has_resource_permission
from apps.giftcards import services
from apps.giftcards.models import GiftCard, GiftCardBatchPage, GiftCardTransaction


class GiftCardsEnabledMixin(ModuleEnabledMixin):
    module_code = "gift_cards"


class TransactionInline(TenantInlineMixin, TabularInline):
    permission_resource = "cash"
    model = GiftCardTransaction
    extra = 0
    fields = ["created_at", "kind", "amount", "balance_after", "order", "note"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class GiftCardTenantAdmin(GiftCardsEnabledMixin, TenantModelAdmin):
    permission_resource = "cash"
    restaurant_field = "restaurant"
    list_display = ["code", "initial_value", "balance", "status_badge", "kind", "recipient", "expires_at", "created_at"]
    list_filter = ["status", "kind", "sold_online"]
    search_fields = ["code", "recipient_name", "purchaser_name", "recipient_phone", "purchaser_phone"]
    ordering = ["-created_at"]
    readonly_fields = [
        "code",
        "initial_value",
        "balance",
        "status",
        "currency",
        "sold_payment",
        "sold_online",
        "token",
        "delivered_at",
        "created_at",
    ]
    fields = [
        "code",
        "status",
        "initial_value",
        "balance",
        "currency",
        "kind",
        "design",
        "expires_at",
        "purchaser_name",
        "purchaser_phone",
        "purchaser_email",
        "recipient_name",
        "recipient_phone",
        "recipient_email",
        "message",
        "notes",
        "sold_online",
        "delivered_at",
        "created_at",
    ]
    inlines = [TransactionInline]
    actions_row = ["resend_row", "void_row"]
    change_list_template = "admin/giftcards/giftcard/change_list.html"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(
        description=_("Status"),
        label={"pending": "warning", "active": "success", "used_up": "info", "void": "danger", "expired": "danger"},
    )
    def status_badge(self, obj):
        return obj.status

    @display(description=_("Recipient"))
    def recipient(self, obj):
        return obj.recipient_name or obj.purchaser_name or "—"

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        if getattr(request, "restaurant", None):
            extra_context["summary"] = services.summary(request.restaurant)
            extra_context["batch_url"] = reverse("tenant_admin:giftcards_giftcardbatchpage_changelist")
        return super().changelist_view(request, extra_context=extra_context)

    def _card(self, request, object_id):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "cash", "update"):
            raise PermissionDenied
        return get_object_or_404(GiftCard, pk=object_id, restaurant=request.restaurant)

    @action(description=_("Resend"), url_path="resend")
    def resend_row(self, request, object_id):
        card = self._card(request, object_id)
        if services.deliver_digital(card, by=request.user, force=True):
            messages.success(request, _("Card {p0} sent again.").format(p0=card.code))
        else:
            messages.error(request, _("No phone / email on this card."))
        return redirect("tenant_admin:giftcards_giftcard_changelist")

    @action(description=_("Void"), url_path="void")
    def void_row(self, request, object_id):
        card = self._card(request, object_id)
        services.void(card, by=request.user, note="Voided from admin")
        messages.success(request, _("Card {p0} voided.").format(p0=card.code))
        return redirect("tenant_admin:giftcards_giftcard_changelist")


class GiftCardBatchTenantAdmin(GiftCardsEnabledMixin, TenantModelAdmin):
    """Print-and-sell: issue N physical cards of one value (paid for later at the till, or pre-paid)."""

    permission_resource = "cash"
    restaurant_field = "restaurant"
    change_list_template = "admin/giftcards/giftcardbatchpage/change_list.html"
    list_display = ["code"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        raise PermissionDenied

    def changelist_view(self, request, extra_context=None):
        if not getattr(request, "restaurant", None) or not self.has_view_permission(request):
            raise PermissionDenied
        extra_context = dict(extra_context or {})
        extra_context.update(
            {
                "create_url": reverse("tenant_admin:giftcards_giftcardbatchpage_create"),
                "can_manage": has_resource_permission(request, "cash", "update"),
                "batch": request.session.pop("giftcards:batch", None),
                "designs": GiftCard.DESIGN_CHOICES,
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        wrap = self.admin_site.admin_view
        return [
            path("create/", wrap(require_POST(self.create_view)), name="giftcards_giftcardbatchpage_create")
        ] + super().get_urls()

    def create_view(self, request):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "cash", "update"):
            raise PermissionDenied
        try:
            count = max(1, min(int(request.POST.get("count") or 1), 200))
            amount = Decimal(request.POST.get("amount") or "0")
        except (ValueError, InvalidOperation):
            messages.error(request, _("Enter a count and an amount."))
            return redirect("tenant_admin:giftcards_giftcardbatchpage_changelist")
        design = request.POST.get("design", "classic")
        note = request.POST.get("note", "")[:200]
        codes = []
        try:
            for _i in range(count):
                card = services.issue(
                    request.restaurant,
                    amount,
                    kind="physical",
                    by=request.user,
                    design=design,
                    notes=note or "Printed batch",
                )
                codes.append(
                    {
                        "code": card.code,
                        "amount": str(card.initial_value),
                        "expires": card.expires_at.strftime("%d.%m.%Y") if card.expires_at else "",
                    }
                )
        except services.GiftCardError as exc:
            messages.error(request, exc.message)
            return redirect("tenant_admin:giftcards_giftcardbatchpage_changelist")
        request.session["giftcards:batch"] = codes
        messages.success(request, _("{p0} card(s) issued. Print the codes below.").format(p0=len(codes)))
        return redirect("tenant_admin:giftcards_giftcardbatchpage_changelist")


def register_giftcards_admin(site):
    site.register(GiftCard, GiftCardTenantAdmin)
    site.register(GiftCardBatchPage, GiftCardBatchTenantAdmin)
