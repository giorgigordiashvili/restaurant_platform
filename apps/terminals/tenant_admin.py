"""Tenant admin: terminals (keys, bridge key shown once), transactions (confirm / cancel / resend), daily reconciliation."""

from __future__ import annotations

from datetime import date

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from unfold.decorators import action, display

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantModelAdmin, has_resource_permission
from apps.terminals import services
from apps.terminals.models import PaymentTerminal, TerminalReconciliation, TerminalTransaction
from apps.terminals.providers import registry

CREDENTIALS = {
    "bog_link": [
        ("client_id", _("BOG client id"), False, _("From the BOG Payment Manager merchant portal.")),
        ("client_secret", _("BOG client secret"), True, ""),
    ],
    "tbc_tpay": [
        ("apikey", _("TBC API key"), True, _("From developers.tbcbank.ge (TPAY)")),
        ("client_id", _("TBC client id"), False, ""),
        ("client_secret", _("TBC client secret"), True, ""),
    ],
}


class TerminalsEnabledMixin(ModuleEnabledMixin):
    module_code = "terminals"


def _cred_field(label, secret, hint=""):
    return forms.CharField(
        required=False,
        label=label,
        help_text=hint,
        widget=forms.PasswordInput(render_value=False) if secret else forms.TextInput(),
    )


class TerminalForm(forms.ModelForm):
    device = forms.CharField(
        required=False,
        label=_("Bridge device"),
        help_text=_("tcp://192.168.1.60:8000 or serial:///dev/ttyUSB0:115200 (ECR bridge only)."),
    )
    bog_link__client_id = _cred_field(_("BOG client id"), False, _("From the BOG Payment Manager merchant portal."))
    bog_link__client_secret = _cred_field(_("BOG client secret"), True)
    tbc_tpay__apikey = _cred_field(_("TBC API key"), True, _("From developers.tbcbank.ge (TPAY)"))
    tbc_tpay__client_id = _cred_field(_("TBC client id"), False)
    tbc_tpay__client_secret = _cred_field(_("TBC client secret"), True)

    class Meta:
        model = PaymentTerminal
        fields = [
            "name",
            "provider",
            "ecr_protocol",
            "terminal_id",
            "is_active",
            "is_default",
            "auto_receipt",
            "timeout_seconds",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["device"].initial = (self.instance.connection or {}).get("device", "")

    def clean(self):
        data = super().clean()
        if data.get("provider") == "ecr_bridge" and not data.get("ecr_protocol"):
            raise forms.ValidationError(_("Pick the ECR protocol (bank) for a bridge terminal."))
        return data


class PaymentTerminalTenantAdmin(TerminalsEnabledMixin, TenantModelAdmin):
    permission_resource = "cash"
    permission_actions = None
    restaurant_field = "restaurant"
    form = TerminalForm
    list_display = ["name", "provider", "configured_badge", "online_badge", "is_default", "is_active", "last_seen_at"]
    list_filter = ["provider", "is_active"]
    actions_row = ["rotate_row"]
    change_list_template = "admin/terminals/paymentterminal/change_list.html"
    change_form_template = "admin/terminals/paymentterminal/change_form.html"
    fieldsets = (
        (None, {"fields": ("name", "provider", "is_default", "is_active", "auto_receipt", "timeout_seconds")}),
        (_("Bank of Georgia pay-by-link"), {"fields": ("bog_link__client_id", "bog_link__client_secret")}),
        (
            _("TBC pay-by-link (TPAY)"),
            {"fields": ("tbc_tpay__apikey", "tbc_tpay__client_id", "tbc_tpay__client_secret")},
        ),
        (_("ECR bridge"), {"fields": ("ecr_protocol", "terminal_id", "device")}),
    )

    def has_add_permission(self, request):
        return self._has_resource_permission(request, "update")

    def has_delete_permission(self, request, obj=None):
        return self._has_resource_permission(request, "update")

    @display(description=_("Keys"), boolean=True)
    def configured_badge(self, obj):
        return registry.is_configured(obj)

    @display(description=_("Online"), boolean=True)
    def online_badge(self, obj):
        return obj.is_online

    def save_model(self, request, obj, form, change):
        if not obj.restaurant_id:
            obj.restaurant = request.restaurant
        device = form.cleaned_data.get("device", "").strip()
        obj.connection = {"device": device} if device else {}
        creds = obj.get_credentials() if change else {}
        for code, fields in CREDENTIALS.items():
            for name, _label, _secret, _hint in fields:
                value = form.cleaned_data.get(f"{code}__{name}", "").strip()
                if value:
                    creds[name] = value
        obj.set_credentials(creds)
        super().save_model(request, obj, form, change)
        if obj.is_default:
            PaymentTerminal.objects.filter(restaurant=obj.restaurant).exclude(pk=obj.pk).update(is_default=False)
        if obj.provider == "ecr_bridge" and not change:
            request.session[f"terminals:key:{obj.pk}"] = obj.bridge_key
            messages.info(
                request,
                _("Bridge key for {p0}: {p1} — copy it into bridge.toml (shown once).").format(
                    p0=obj.name, p1=obj.bridge_key
                ),
            )

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        if getattr(request, "restaurant", None):
            from django.conf import settings

            extra_context["server_url"] = getattr(settings, "PUBLIC_API_BASE_URL", "")
            extra_context["bog_callback"] = (
                f"{getattr(settings, 'PUBLIC_API_BASE_URL', '')}{reverse('terminals:bog-callback')}"
            )
            extra_context["tbc_callback"] = (
                f"{getattr(settings, 'PUBLIC_API_BASE_URL', '')}{reverse('terminals:tbc-callback')}"
            )
        return super().changelist_view(request, extra_context=extra_context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = dict(extra_context or {})
        if object_id:
            key = request.session.pop(f"terminals:key:{object_id}", None)
            if key:
                extra_context["bridge_key_once"] = key
            obj = PaymentTerminal.objects.filter(pk=object_id).first()
            if obj is not None:
                creds = obj.get_credentials()
                extra_context["keys_set"] = sorted(k for k in creds if not k.endswith("_url"))
        return super().changeform_view(request, object_id, form_url, extra_context)

    @action(description=_("Rotate bridge key"), url_path="rotate-key")
    def rotate_row(self, request, object_id):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "cash", "update"):
            raise PermissionDenied
        t = get_object_or_404(PaymentTerminal, pk=object_id, restaurant=request.restaurant)
        t.rotate_key()
        request.session[f"terminals:key:{t.pk}"] = t.bridge_key
        messages.success(request, _("New bridge key for {p0}: {p1} (shown once).").format(p0=t.name, p1=t.bridge_key))
        return redirect("tenant_admin:terminals_paymentterminal_change", object_id=t.pk)


class TerminalTransactionTenantAdmin(TerminalsEnabledMixin, TenantModelAdmin):
    permission_resource = "cash"
    restaurant_field = "restaurant"
    list_display = ["created_at", "terminal", "kind", "amount", "tip", "status_badge", "target", "card_mask", "receipt"]
    list_filter = ["status", "kind", "terminal"]
    search_fields = ["external_id", "order__order_number", "card_mask", "rrn"]
    ordering = ["-created_at"]
    actions_row = ["confirm_row", "cancel_row", "resend_row"]
    readonly_fields = [f.name for f in TerminalTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(
        description=_("Status"),
        label={
            "pending": "info",
            "sent": "warning",
            "awaiting_confirm": "warning",
            "approved": "success",
            "declined": "danger",
            "cancelled": "danger",
            "timeout": "danger",
            "failed": "danger",
        },
    )
    def status_badge(self, obj):
        return obj.status

    @display(description=_("Bill"))
    def target(self, obj):
        return obj.target_label

    @display(description=_("Receipt"))
    def receipt(self, obj):
        return obj.payment.receipt_number if obj.payment_id else "—"

    def _tx(self, request, object_id):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "cash", "create"):
            raise PermissionDenied
        return get_object_or_404(
            TerminalTransaction.objects.select_related("terminal"), pk=object_id, restaurant=request.restaurant
        )

    def _back(self):
        return redirect("tenant_admin:terminals_terminaltransaction_changelist")

    @action(description=_("Confirm approved"), url_path="confirm")
    def confirm_row(self, request, object_id):
        tx = self._tx(request, object_id)
        try:
            services.confirm_manual(tx, by=request.user)
            messages.success(
                request, _("Payment booked: {p0}.").format(p0=tx.payment.receipt_number if tx.payment_id else tx.pk)
            )
        except services.TerminalServiceError as exc:
            messages.error(request, exc.message)
        return self._back()

    @action(description=_("Cancel"), url_path="cancel")
    def cancel_row(self, request, object_id):
        tx = self._tx(request, object_id)
        services.cancel(tx, by=request.user)
        messages.success(request, _("Transaction cancelled."))
        return self._back()

    @action(description=_("Resend link"), url_path="resend")
    def resend_row(self, request, object_id):
        tx = self._tx(request, object_id)
        if not tx.pay_url or not tx.sent_to:
            messages.error(request, _("No link or no phone / email on this transaction."))
        else:
            services.send_pay_link(tx, tx.sent_to, by=request.user)
            messages.success(request, _("Link sent again to {p0}.").format(p0=tx.sent_to))
        return self._back()


class TerminalReconciliationTenantAdmin(TerminalsEnabledMixin, TenantModelAdmin):
    permission_resource = "cash"
    restaurant_field = "restaurant"
    change_list_template = "admin/terminals/terminalreconciliation/change_list.html"
    list_display = ["id"]

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
        raw = request.GET.get("day", "")
        try:
            day = date.fromisoformat(raw) if raw else timezone.localdate()
        except ValueError:
            day = timezone.localdate()
        extra_context = dict(extra_context or {})
        extra_context.update({"day": day, "report": services.reconcile(request.restaurant, day)})
        return super().changelist_view(request, extra_context=extra_context)


def register_terminals_admin(site):
    site.register(PaymentTerminal, PaymentTerminalTenantAdmin)
    site.register(TerminalTransaction, TerminalTransactionTenantAdmin)
    site.register(TerminalReconciliation, TerminalReconciliationTenantAdmin)
