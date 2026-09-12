"""Tenant admin: suppliers (+ price list), purchase orders (lines, send / receive / cancel), 'PO from buy list'."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from unfold.admin import TabularInline
from unfold.decorators import action, display

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantInlineMixin, TenantModelAdmin, has_resource_permission
from apps.inventory.models import StockItem, UnitOfMeasure
from apps.purchasing import services
from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine, Supplier, SupplierItem


class PurchasingEnabledMixin(ModuleEnabledMixin):
    module_code = "purchasing"


class SupplierItemInline(TenantInlineMixin, TabularInline):
    permission_resource = "warehouse"
    model = SupplierItem
    extra = 0
    fields = ["stock_item", "supplier_sku", "unit", "pack_qty", "price", "is_preferred", "last_price_at"]
    readonly_fields = ["last_price_at"]
    autocomplete_fields = ["stock_item"]

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        restaurant = getattr(request, "restaurant", None)
        if "stock_item" in formset.form.base_fields and restaurant is not None:
            formset.form.base_fields["stock_item"].queryset = StockItem.objects.filter(
                restaurant=restaurant, is_active=True
            )
        return formset


class SupplierTenantAdmin(PurchasingEnabledMixin, TenantModelAdmin):
    permission_resource = "warehouse"
    restaurant_field = "restaurant"
    list_display = ["name", "contact_name", "phone", "email", "lead_days", "items_count", "open_orders", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "contact_name", "phone", "email"]
    inlines = [SupplierItemInline]
    fields = [
        "name",
        "contact_name",
        "phone",
        "email",
        "tax_id",
        "address",
        "payment_terms",
        "lead_days",
        "notes",
        "is_active",
    ]

    @display(description=_("Items"))
    def items_count(self, obj):
        return obj.items.count()

    @display(description=_("Open orders"))
    def open_orders(self, obj):
        return obj.orders.filter(status__in=PurchaseOrder.OPEN).count()

    def save_model(self, request, obj, form, change):
        if not obj.restaurant_id:
            obj.restaurant = request.restaurant
        super().save_model(request, obj, form, change)


class PurchaseOrderLineInline(TenantInlineMixin, TabularInline):
    permission_resource = "warehouse"
    model = PurchaseOrderLine
    extra = 1
    fields = ["stock_item", "quantity", "unit", "unit_price", "received_qty", "note"]
    readonly_fields = ["received_qty"]
    autocomplete_fields = ["stock_item"]

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        restaurant = getattr(request, "restaurant", None)
        if "stock_item" in formset.form.base_fields and restaurant is not None:
            formset.form.base_fields["stock_item"].queryset = StockItem.objects.filter(
                restaurant=restaurant, is_active=True
            )
        return formset

    def has_add_permission(self, request, obj=None):
        return (obj is None or obj.status == "draft") and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return (obj is None or obj.status == "draft") and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return (obj is None or obj.status == "draft") and super().has_delete_permission(request, obj)


class PurchaseOrderTenantAdmin(PurchasingEnabledMixin, TenantModelAdmin):
    permission_resource = "warehouse"
    restaurant_field = "restaurant"
    list_display = [
        "number",
        "supplier",
        "status_badge",
        "expected_on",
        "lines_count",
        "subtotal",
        "sent_at",
        "received_at",
    ]
    list_filter = ["status", "supplier"]
    search_fields = ["number", "supplier__name", "reference"]
    inlines = [PurchaseOrderLineInline]
    fields = ["number", "supplier", "status", "expected_on", "notes", "reference", "subtotal", "sent_at", "received_at"]
    readonly_fields = ["number", "status", "subtotal", "sent_at", "received_at"]
    actions_row = ["send", "receive", "text", "cancel"]
    change_list_template = "admin/purchasing/purchaseorder/change_list.html"

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "supplier" in form.base_fields:
            form.base_fields["supplier"].queryset = Supplier.objects.filter(
                restaurant=getattr(request, "restaurant", None), is_active=True
            )
        return form

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.status != "draft":
            ro += ["supplier", "expected_on", "notes"]
        return ro

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        if getattr(request, "restaurant", None):
            extra_context["summary"] = services.open_summary(request.restaurant)
            extra_context["from_buy_list_url"] = reverse("tenant_admin:purchasing_purchaseorder_from_buy_list")
            extra_context["can_create"] = has_resource_permission(request, "warehouse", "create")
        return super().changelist_view(request, extra_context=extra_context)

    @display(
        description=_("Status"),
        label={"draft": "info", "sent": "warning", "partial": "warning", "received": "success", "cancelled": "danger"},
    )
    def status_badge(self, obj):
        return obj.status

    @display(description=_("Lines"))
    def lines_count(self, obj):
        return obj.lines.count()

    def save_model(self, request, obj, form, change):
        if not obj.restaurant_id:
            obj.restaurant = request.restaurant
        if not obj.number:
            obj.number = services.next_po_number(request.restaurant)
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.recompute()

    def get_urls(self):
        wrap = self.admin_site.admin_view
        n = "purchasing_purchaseorder"
        custom = [
            path("from-buy-list/", wrap(require_POST(self.from_buy_list_view)), name=f"{n}_from_buy_list"),
            path("<uuid:object_id>/receive/", wrap(self.receive_view), name=f"{n}_receive_page"),
        ]
        return custom + super().get_urls()

    def _po(self, request, object_id, action_needed="update"):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "warehouse", action_needed):
            raise PermissionDenied
        return get_object_or_404(PurchaseOrder, pk=object_id, restaurant=request.restaurant)

    def _back(self):
        return redirect("tenant_admin:purchasing_purchaseorder_changelist")

    def from_buy_list_view(self, request):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "warehouse", "create"):
            raise PermissionDenied
        for_date = None
        raw = request.POST.get("for_date")
        if raw:
            try:
                for_date = timezone.datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                for_date = None
        orders = services.orders_from_buy_list(request.restaurant, by=request.user, for_date=for_date)
        if not orders:
            messages.info(request, _("Everything is at par -- nothing to order."))
        else:
            messages.success(
                request,
                _("Created %(n)d draft purchase order(s): %(numbers)s")
                % {"n": len(orders), "numbers": ", ".join(o.number for o in orders)},
            )
        return self._back()

    @action(description=_("Send"), url_path="send")
    def send(self, request, object_id):
        po = self._po(request, object_id)
        try:
            result = services.send_order(po, by=request.user)
        except services.PurchasingError as exc:
            messages.error(request, exc.message)
            return self._back()
        if result["via"] == "manual":
            messages.info(
                request,
                _("%(number)s marked as sent. The supplier has no email / phone: open 'Text' to copy the order.")
                % {"number": po.number},
            )
        else:
            messages.success(request, _("%(number)s sent by %(via)s.") % {"number": po.number, "via": result["via"]})
        return self._back()

    @action(description=_("Receive"), url_path="receive")
    def receive(self, request, object_id):
        return redirect("tenant_admin:purchasing_purchaseorder_receive_page", object_id=object_id)

    @action(description=_("Text"), url_path="text")
    def text(self, request, object_id):
        po = self._po(request, object_id, "read")
        return HttpResponse(services.render_text(po), content_type="text/plain; charset=utf-8")

    @action(description=_("Cancel"), url_path="cancel")
    def cancel(self, request, object_id):
        po = self._po(request, object_id)
        try:
            services.cancel_order(po, by=request.user)
            messages.success(request, _("%(number)s cancelled.") % {"number": po.number})
        except services.PurchasingError as exc:
            messages.error(request, exc.message)
        return self._back()

    def receive_view(self, request, object_id):
        """GET: per-line quantity / price / expiry form. POST: book the delivery."""
        po = self._po(request, object_id, "create")
        lines = list(po.lines.select_related("stock_item", "unit"))
        if request.method == "POST":
            rows = []
            for line in lines:
                raw = request.POST.get(f"qty_{line.pk}", "").strip()
                if not raw:
                    continue
                try:
                    qty = Decimal(raw)
                except InvalidOperation:
                    messages.error(request, _("Bad quantity for %(item)s.") % {"item": line.stock_item.name})
                    return redirect("tenant_admin:purchasing_purchaseorder_receive_page", object_id=po.pk)
                price_raw = request.POST.get(f"price_{line.pk}", "").strip()
                expiry_raw = request.POST.get(f"expiry_{line.pk}", "").strip()
                expiry = None
                if expiry_raw:
                    try:
                        expiry = timezone.datetime.strptime(expiry_raw, "%Y-%m-%d").date()
                    except ValueError:
                        expiry = None
                rows.append({"line": line, "quantity": qty, "unit_price": price_raw or None, "expiry_date": expiry})
            try:
                lots = services.receive_order(
                    po, rows, by=request.user, reference=request.POST.get("reference", "").strip()
                )
            except services.PurchasingError as exc:
                messages.error(request, exc.message)
                return redirect("tenant_admin:purchasing_purchaseorder_receive_page", object_id=po.pk)
            except Exception as exc:  # InventoryError
                messages.error(request, str(exc))
                return redirect("tenant_admin:purchasing_purchaseorder_receive_page", object_id=po.pk)
            po.refresh_from_db()
            messages.success(
                request,
                _("Booked %(n)d lot(s); %(number)s is now %(status)s.")
                % {"n": len(lots), "number": po.number, "status": po.get_status_display()},
            )
            return self._back()
        request.current_app = self.admin_site.name
        context = {
            **self.admin_site.each_context(request),
            "title": _("Receive %(number)s") % {"number": po.number},
            "po": po,
            "lines": lines,
            "back_url": reverse("tenant_admin:purchasing_purchaseorder_changelist"),
            "currency": getattr(request.restaurant, "default_currency", "GEL"),
        }
        return render(request, "admin/purchasing/purchaseorder/receive.html", context)


def register_purchasing_admin(site):
    site.register(Supplier, SupplierTenantAdmin)
    site.register(PurchaseOrder, PurchaseOrderTenantAdmin)
