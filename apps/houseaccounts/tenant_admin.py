"""Tenant admin: accounts with ledger inline and Charge / Settle / Statement actions, statements list."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from unfold.admin import TabularInline
from unfold.decorators import action, display

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantInlineMixin, TenantModelAdmin, has_resource_permission
from apps.houseaccounts import services
from apps.houseaccounts.models import HouseAccount, HouseAccountEntry, HouseAccountStatement


class HouseAccountsEnabledMixin(ModuleEnabledMixin):
    module_code = "house_accounts"


class EntryInline(TenantInlineMixin, TabularInline):
    permission_resource = "cash"
    model = HouseAccountEntry
    extra = 0
    fields = ["created_at", "kind", "amount", "balance_after", "order", "signed_by", "note"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class HouseAccountTenantAdmin(HouseAccountsEnabledMixin, TenantModelAdmin):
    permission_resource = "cash"
    restaurant_field = "restaurant"
    list_display = [
        "name",
        "company",
        "phone",
        "balance",
        "credit_limit",
        "status_badge",
        "overdue_badge",
        "last_payment_at",
    ]
    list_filter = ["status"]
    search_fields = ["name", "company", "phone", "email"]
    ordering = ["name"]
    fields = [
        "name",
        "company",
        "tax_id",
        "phone",
        "email",
        "credit_limit",
        "billing_day",
        "authorised_names",
        "require_signature",
        "customer",
        "notes",
        "status",
        "balance",
        "last_payment_at",
    ]
    readonly_fields = ["balance", "last_payment_at"]
    inlines = [EntryInline]
    actions_row = ["settle_row", "statement_row"]
    change_list_template = "admin/houseaccounts/houseaccount/change_list.html"

    def has_add_permission(self, request):
        return self._has_resource_permission(request, "update")

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description=_("Status"), label={"active": "success", "suspended": "warning", "closed": "danger"})
    def status_badge(self, obj):
        return obj.status

    @display(description=_("Overdue"), boolean=True)
    def overdue_badge(self, obj):
        from django.utils import timezone

        return (
            obj.status == "active"
            and obj.balance > 0
            and (obj.last_payment_at or obj.created_at) < timezone.now() - timezone.timedelta(days=30)
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "customer" and getattr(request, "restaurant", None):
            from apps.crm.models import Customer

            kwargs["queryset"] = Customer.objects.filter(restaurant=request.restaurant)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        if getattr(request, "restaurant", None):
            extra_context["summary"] = services.summary(request.restaurant)
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        wrap = self.admin_site.admin_view
        n = "houseaccounts_houseaccount"
        return [
            path("<uuid:object_id>/settle-page/", wrap(self.settle_page), name=f"{n}_settle_page"),
        ] + super().get_urls()

    def _account(self, request, object_id, action_needed="create"):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "cash", action_needed):
            raise PermissionDenied
        return get_object_or_404(HouseAccount, pk=object_id, restaurant=request.restaurant)

    @action(description=_("Settle"), url_path="settle")
    def settle_row(self, request, object_id):
        return redirect("tenant_admin:houseaccounts_houseaccount_settle_page", object_id=object_id)

    @action(description=_("Statement"), url_path="statement")
    def statement_row(self, request, object_id):
        account = self._account(request, object_id, "update")
        start, end = services.previous_month()
        stmt = services.build_statement(account, start, end)
        sent = services.send_statement(stmt, by=request.user, force=True)
        messages.success(
            request,
            _("Statement {p0} – {p1} built{p2}.").format(
                p0=start, p1=end, p2=_(" and sent") if sent else _(" (no phone / email to send to)")
            ),
        )
        return redirect("tenant_admin:houseaccounts_houseaccountstatement_changelist")

    def settle_page(self, request, object_id):
        account = self._account(request, object_id)
        if request.method == "POST":
            try:
                amount = Decimal(request.POST.get("amount") or "0")
            except InvalidOperation:
                amount = Decimal("0")
            method = request.POST.get("method", "cash")
            try:
                if request.POST.get("writeoff"):
                    services.adjust(
                        account,
                        -amount,
                        by=request.user,
                        note=request.POST.get("note", "") or "Written off",
                        writeoff=True,
                    )
                    messages.success(request, _("{p0} written off.").format(p0=amount))
                else:
                    services.settle(account, amount, method=method, by=request.user, note=request.POST.get("note", ""))
                    messages.success(request, _("{p0} received from {p1}.").format(p0=amount, p1=account.name))
            except services.HouseAccountError as exc:
                messages.error(request, exc.message)
            except Exception as exc:  # LedgerError
                messages.error(request, str(exc))
            return redirect("tenant_admin:houseaccounts_houseaccount_changelist")
        request.current_app = self.admin_site.name
        context = {
            **self.admin_site.each_context(request),
            "title": _("Settle {p0}").format(p0=account.name),
            "account": account,
            "back_url": reverse("tenant_admin:houseaccounts_houseaccount_changelist"),
        }
        return render(request, "admin/houseaccounts/houseaccount/settle.html", context)


class HouseAccountStatementTenantAdmin(HouseAccountsEnabledMixin, TenantModelAdmin):
    permission_resource = "cash"
    restaurant_field = "account__restaurant"
    list_display = ["account", "period_start", "period_end", "opening", "charges", "payments", "closing", "sent_at"]
    list_filter = ["account"]
    ordering = ["-period_end"]
    readonly_fields = [f.name for f in HouseAccountStatement._meta.fields]
    actions_row = ["send_row", "open_row"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("account")

    def _stmt(self, request, object_id):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "cash", "update"):
            raise PermissionDenied
        return get_object_or_404(HouseAccountStatement, pk=object_id, account__restaurant=request.restaurant)

    @action(description=_("Send"), url_path="send")
    def send_row(self, request, object_id):
        stmt = self._stmt(request, object_id)
        if services.send_statement(stmt, by=request.user, force=True):
            messages.success(request, _("Statement sent to {p0}.").format(p0=stmt.sent_to))
        else:
            messages.error(request, _("No phone / email on the account."))
        return redirect("tenant_admin:houseaccounts_houseaccountstatement_changelist")

    @action(description=_("Open"), url_path="open")
    def open_row(self, request, object_id):
        stmt = self._stmt(request, object_id)
        return redirect(services.statement_url(stmt))


def register_houseaccounts_admin(site):
    site.register(HouseAccount, HouseAccountTenantAdmin)
    site.register(HouseAccountStatement, HouseAccountStatementTenantAdmin)
