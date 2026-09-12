"""
Warehouse pages of the tenant admin.

Every write goes through apps.inventory.services (the same code the order
flow uses), so the ledger and the cached counters stay consistent no matter
which form was used. Nothing here is visible while the restaurant's
``warehouse_enabled`` flag is off.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from unfold.admin import TabularInline as UnfoldTabularInline
from unfold.decorators import display

from apps.core.tenant_admin_base import (
    UNFOLD_INPUT_CLASSES,
    TenantForeignKeyScopingMixin,
    TenantInlineMixin,
    TenantModelAdmin,
    has_resource_permission,
)
from apps.inventory import hooks, services
from apps.inventory.exceptions import InventoryError, UnitMismatch
from apps.inventory.models import (
    EmployeeMeal,
    InventoryAlert,
    InventoryAlertPlatformTask,
    RecipeLine,
    RestaurantDeliveryPlatform,
    StockAdjustment,
    StockItem,
    StockLot,
    StockMovement,
    UnitOfMeasure,
    WarehouseOverview,
    WasteEntry,
    fmt_qty,
)
from apps.menu.models import MenuItem, Modifier
from apps.staff.models import StaffMember


def _membership(request):
    restaurant = getattr(request, "restaurant", None)
    if not restaurant or not request.user.is_authenticated:
        return None
    return StaffMember.objects.filter(user=request.user, restaurant=restaurant, is_active=True).first()


_fmt = fmt_qty


class WarehouseEnabledMixin:
    """Hide the whole warehouse until the restaurant switches it on in Settings."""

    def _warehouse_on(self, request):
        restaurant = getattr(request, "restaurant", None)
        return bool(restaurant and restaurant.warehouse_enabled)

    def has_module_permission(self, request):
        return self._warehouse_on(request) and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._warehouse_on(request) and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return self._warehouse_on(request) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._warehouse_on(request) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._warehouse_on(request) and super().has_delete_permission(request, obj)


class ReadOnlyAfterSaveMixin:
    """Documents (waste, meals, counts) are immutable once posted -- corrections are new documents."""

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


def _style(form):
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, (forms.TextInput, forms.NumberInput, forms.DateInput, forms.Textarea, forms.Select)):
            widget.attrs["class"] = (widget.attrs.get("class", "") + " " + UNFOLD_INPUT_CLASSES).strip()


# ── Stock items ───────────────────────────────────────────────────────────


class StockLotInline(TenantInlineMixin, UnfoldTabularInline):
    """Open lots of a stock item, read-only (receive stock through 'Stock lots (receiving)')."""

    permission_resource = "warehouse"
    permission_actions = {"view": "read", "add": "update", "change": "update", "delete": "update"}
    model = StockLot
    extra = 0
    can_delete = False
    fields = ["received_at", "received_qty", "remaining_qty", "unit_cost", "expiry_date", "supplier_name", "reference"]
    readonly_fields = fields
    ordering = ["expiry_date", "received_at"]
    verbose_name_plural = "Open lots (oldest expiry first)"

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(remaining_qty__gt=0)


class StockItemTenantAdmin(WarehouseEnabledMixin, TenantModelAdmin):
    permission_resource = "warehouse"
    list_display = [
        "name",
        "category",
        "base_unit",
        "on_hand_display",
        "reserved_display",
        "available_display",
        "min_level",
        "par_level",
        "level_badge",
        "is_active",
    ]
    list_editable = ["min_level", "par_level", "is_active"]
    list_filter = ["is_active", "category", "base_unit"]
    search_fields = ["name", "sku", "category"]
    ordering = ["name"]
    inlines = [StockLotInline]
    readonly_fields = ["on_hand_display", "reserved_display", "available_display", "cost_display", "used_in"]
    fieldsets = (
        (None, {"fields": ("name", "sku", "category", "base_unit", "is_active")}),
        (
            "Stock",
            {
                "fields": ("on_hand_display", "reserved_display", "available_display", "cost_display", "used_in"),
                "description": "Reserved = held by orders waiting for the kitchen. Available = on hand − reserved.",
            },
        ),
        (
            "Levels",
            {
                "fields": ("min_level", "par_level", "expiry_warning_days"),
                "description": "In the base unit. Min: alert below this. Par: the buy list tops stock up to this.",
            },
        ),
        (
            "Purchasing",
            {
                "fields": ("purchase_unit", "purchase_pack_qty", "supplier_name", "default_unit_cost"),
                "description": "How you buy it (e.g. kg, 10 per bag) -- the buy list is written in these packs.",
            },
        ),
    )

    def has_delete_permission(self, request, obj=None):
        # Ledger rows point at items (PROTECT); deactivate instead.
        return False

    @display(description="On hand")
    def on_hand_display(self, obj):
        return _fmt(obj.on_hand_qty, obj.base_unit)

    @display(description="Reserved")
    def reserved_display(self, obj):
        return _fmt(obj.reserved_qty, obj.base_unit)

    @display(description="Available")
    def available_display(self, obj):
        return _fmt(obj.available_qty, obj.base_unit)

    @display(description="Cost per unit")
    def cost_display(self, obj):
        cost = obj.current_unit_cost()
        return f"{cost:.4f} / {obj.base_unit.code}" if cost is not None else "—"

    @display(description="Level", label={"out": "danger", "low": "warning", "ok": "success"})
    def level_badge(self, obj):
        return obj.level

    @display(description="Used in")
    def used_in(self, obj):
        lines = RecipeLine.objects.filter(stock_item=obj).select_related("menu_item", "modifier", "unit")
        parts = []
        for line in lines:
            target = line.menu_item or line.modifier
            parts.append(f"{target} ({_fmt(line.quantity, line.unit)})")
        return ", ".join(parts) if parts else "No recipe uses this item yet."


# ── Receiving (stock lots) ────────────────────────────────────────────────


class OpenLotsFilter(admin.SimpleListFilter):
    title = "lots"
    parameter_name = "open"

    def lookups(self, request, model_admin):
        return [("open", "Open (stock left)"), ("all", "All")]

    def choices(self, changelist):
        for lookup, title in self.lookup_choices:
            yield {
                "selected": (self.value() or "open") == lookup,
                "query_string": changelist.get_query_string({self.parameter_name: lookup}),
                "display": title,
            }

    def queryset(self, request, queryset):
        if (self.value() or "open") == "open":
            return queryset.filter(remaining_qty__gt=0)
        return queryset


class ReceiveStockForm(forms.ModelForm):
    quantity = forms.DecimalField(min_value=Decimal("0.0001"), decimal_places=4, max_digits=14)
    unit = forms.ModelChoiceField(queryset=UnitOfMeasure.objects.all(), help_text="Unit of the quantity above.")
    unit_cost_entered = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=4,
        max_digits=12,
        label="Cost per unit",
        help_text="Per the unit above.",
    )
    total_cost_entered = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        max_digits=10,
        label="Total cost",
        help_text="Or the invoice total.",
    )

    class Meta:
        model = StockLot
        fields = ["stock_item", "expiry_date", "supplier_name", "reference", "notes"]
        widgets = {"expiry_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)

    def clean(self):
        cleaned = super().clean()
        item, unit = cleaned.get("stock_item"), cleaned.get("unit")
        if item and unit and unit.dimension != item.base_unit.dimension:
            self.add_error("unit", f"{item.name} is tracked in {item.base_unit.code}; pick a matching unit.")
        if item and not item.is_active:
            self.add_error("stock_item", "This item is inactive.")
        return cleaned


class StockLotTenantAdmin(WarehouseEnabledMixin, TenantModelAdmin):
    """'Receive stock': the add form books a delivery as a lot."""

    permission_resource = "warehouse"
    form = ReceiveStockForm
    list_display = [
        "stock_item",
        "received_at",
        "received_display",
        "remaining_display",
        "unit_cost",
        "total_cost",
        "expiry_badge",
        "supplier_name",
        "reference",
    ]
    list_filter = [OpenLotsFilter, "stock_item"]
    search_fields = ["stock_item__name", "reference", "supplier_name"]
    ordering = ["-received_at"]
    date_hierarchy = "received_at"
    autocomplete_fields = ["stock_item"]

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return (
                (
                    "Receive stock",
                    {
                        "fields": ("stock_item", "quantity", "unit", "unit_cost_entered", "total_cost_entered"),
                        "description": "Enter the quantity as bought (5 kg, 2 l, 12 pcs). Give either the cost per unit or the invoice total.",
                    },
                ),
                ("Details", {"fields": ("expiry_date", "supplier_name", "reference", "notes")}),
            )
        return (
            (
                None,
                {"fields": ("stock_item", "received_at", "received_qty", "remaining_qty", "unit_cost", "total_cost")},
            ),
            ("Details", {"fields": ("expiry_date", "supplier_name", "reference", "notes")}),
        )

    def get_form(self, request, obj=None, **kwargs):
        if obj is not None:
            kwargs["form"] = forms.ModelForm
            kwargs["fields"] = ["expiry_date", "supplier_name", "reference", "notes"]
        return super().get_form(request, obj, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ["stock_item", "received_at", "received_qty", "remaining_qty", "unit_cost", "total_cost"]

    def has_delete_permission(self, request, obj=None):
        return False

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "stock_item":
            kwargs["queryset"] = StockItem.objects.filter(restaurant=request.restaurant, is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        data = form.cleaned_data
        services.receive_stock(
            data["stock_item"],
            data["quantity"],
            data["unit"],
            unit_cost=data.get("unit_cost_entered"),
            total_cost=data.get("total_cost_entered"),
            expiry_date=data.get("expiry_date"),
            supplier_name=data.get("supplier_name", ""),
            reference=data.get("reference", ""),
            notes=data.get("notes", ""),
            by=request.user,
            lot=obj,
        )
        messages.success(request, f"Received {_fmt(data['quantity'], data['unit'])} of {data['stock_item'].name}.")

    @display(description="Received")
    def received_display(self, obj):
        return _fmt(obj.received_qty, obj.stock_item.base_unit)

    @display(description="Remaining")
    def remaining_display(self, obj):
        return _fmt(obj.remaining_qty, obj.stock_item.base_unit)

    @display(description="Expiry", label={"expired": "danger", "soon": "warning", "ok": "success", "none": "info"})
    def expiry_badge(self, obj):
        days = obj.days_to_expiry
        if days is None:
            return "none"
        if days < 0:
            return "expired"
        if days <= obj.stock_item.expiry_warning_days:
            return "soon"
        return "ok"


# ── Waste / meals / counts ────────────────────────────────────────────────


class _DocumentAdmin(WarehouseEnabledMixin, ReadOnlyAfterSaveMixin, TenantModelAdmin):
    """Shared plumbing for the three self-service documents."""

    service = None  # callable(obj, by=user)
    exclude_on_form = ("restaurant",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "stock_item":
            kwargs["queryset"] = StockItem.objects.filter(restaurant=request.restaurant, is_active=True)
        if db_field.name == "lot":
            kwargs["queryset"] = StockLot.objects.filter(restaurant=request.restaurant, remaining_qty__gt=0)
        if db_field.name == "menu_item":
            kwargs["queryset"] = MenuItem.objects.filter(restaurant=request.restaurant)
        if db_field.name == "staff_member":
            kwargs["queryset"] = StaffMember.objects.filter(restaurant=request.restaurant, is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)

        class Styled(form_class):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                _style(self)

        return Styled

    def save_model(self, request, obj, form, change):
        if change:
            return
        obj.restaurant = request.restaurant
        try:
            type(self).service(obj, by=request.user)
        except (InventoryError, UnitMismatch, DjangoValidationError) as exc:
            # Model clean() already ran, so this is a service rule (no recipe,
            # inactive item...). Surface it and leave nothing behind.
            messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
            obj.pk = None
            raise PermissionDenied("Could not record this entry.")


class WasteEntryTenantAdmin(_DocumentAdmin):
    permission_resource = "warehouse_logs"
    service = staticmethod(services.record_waste)
    list_display = ["occurred_on", "stock_item", "quantity_display", "reason", "reported_by", "total_cost", "note"]
    list_filter = ["reason", "stock_item"]
    search_fields = ["stock_item__name", "note"]
    ordering = ["-occurred_on", "-created_at"]
    fields = ["stock_item", "quantity", "unit", "reason", "lot", "occurred_on", "note"]

    def get_readonly_fields(self, request, obj=None):
        return ["reported_by", "total_cost"] if obj else []

    def save_model(self, request, obj, form, change):
        obj.reported_by = _membership(request)
        super().save_model(request, obj, form, change)

    @display(description="Quantity")
    def quantity_display(self, obj):
        return _fmt(obj.quantity, obj.unit)


class EmployeeMealTenantAdmin(_DocumentAdmin):
    permission_resource = "warehouse_logs"
    service = staticmethod(services.record_employee_meal)
    list_display = ["meal_date", "staff_member", "what", "quantity", "total_cost", "recorded_by"]
    list_filter = ["staff_member", "meal_date"]
    search_fields = ["staff_member__user__email", "staff_member__user__first_name", "menu_item__translations__name"]
    ordering = ["-meal_date", "-created_at"]
    autocomplete_fields = ["menu_item"]
    fieldsets = (
        (None, {"fields": ("staff_member", "meal_date", "note")}),
        ("A dish from the menu", {"fields": ("menu_item", "quantity")}),
        (
            "…or a raw ingredient",
            {
                "fields": ("stock_item", "unit"),
                "description": "Leave the dish empty and pick a stock item + unit instead.",
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        return ["recorded_by", "total_cost"] if obj else []

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        me = _membership(request)
        if me:
            initial.setdefault("staff_member", me.pk)
        return initial

    @display(description="Ate")
    def what(self, obj):
        return str(obj.menu_item or obj.stock_item)


class StockAdjustmentTenantAdmin(_DocumentAdmin):
    permission_resource = "warehouse"
    service = staticmethod(services.apply_adjustment)
    list_display = ["created_at", "stock_item", "mode", "quantity_display", "delta_display", "reason", "made_by"]
    list_filter = ["mode", "reason", "stock_item"]
    search_fields = ["stock_item__name", "note"]
    fields = ["stock_item", "mode", "quantity", "unit", "reason", "note"]

    def get_readonly_fields(self, request, obj=None):
        return ["made_by", "delta_base"] if obj else []

    @display(description="Entered")
    def quantity_display(self, obj):
        return _fmt(obj.quantity, obj.unit)

    @display(description="Change")
    def delta_display(self, obj):
        sign = "+" if obj.delta_base > 0 else ""
        return f"{sign}{_fmt(obj.delta_base, obj.stock_item.base_unit)}"


# ── Ledger & alerts ───────────────────────────────────────────────────────


class StockMovementTenantAdmin(WarehouseEnabledMixin, TenantModelAdmin):
    permission_resource = "warehouse"
    list_display = [
        "created_at",
        "kind",
        "reason",
        "stock_item",
        "quantity_display",
        "total_cost",
        "order_link",
        "staff_user",
        "note",
    ]
    list_filter = ["kind", "reason", "stock_item"]
    search_fields = ["stock_item__name", "order__order_number", "note"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description="Quantity")
    def quantity_display(self, obj):
        sign = "+" if obj.quantity > 0 else ""
        return f"{sign}{_fmt(obj.quantity, obj.stock_item.base_unit)}"

    @display(description="Order")
    def order_link(self, obj):
        return obj.order.order_number if obj.order_id else "—"


class InventoryAlertTenantAdmin(WarehouseEnabledMixin, TenantModelAdmin):
    permission_resource = "warehouse"
    list_display = ["created_at", "kind", "status", "message", "tasks_summary"]
    list_filter = ["kind", "status"]
    search_fields = ["message"]
    ordering = ["-created_at"]
    actions = ["mark_done"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description="Platforms")
    def tasks_summary(self, obj):
        tasks = list(obj.platform_tasks.select_related("platform"))
        if not tasks:
            return "—"
        return ", ".join(f"{t.platform}: {'✓' if t.is_done else t.get_action_display()}" for t in tasks)

    @admin.action(description="Mark selected alerts done")
    def mark_done(self, request, queryset):
        if not has_resource_permission(request, "warehouse", "update"):
            raise PermissionDenied
        n = 0
        for alert in queryset.filter(status="open"):
            alert.resolve(by=request.user)
            n += 1
        self.message_user(request, f"{n} alert(s) marked done.")


# ── Overview page ─────────────────────────────────────────────────────────


class WarehouseOverviewTenantAdmin(WarehouseEnabledMixin, TenantModelAdmin):
    """
    The warehouse dashboard: stock health, expiring lots, tomorrow's buy
    list and the alert feed with its delivery-platform checklists. The stock
    changelist underneath is the alert history.
    """

    permission_resource = "warehouse"
    change_list_template = "admin/inventory/warehouseoverview/change_list.html"
    list_display = ["created_at", "kind", "status", "message"]
    list_filter = ["kind", "status"]
    ordering = ["-created_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context.update(self._page_context(request))
        return super().changelist_view(request, extra_context=extra_context)

    def _page_context(self, request):
        restaurant = request.restaurant
        for_date = timezone.localdate() + timezone.timedelta(days=1)
        raw = request.GET.get("for_date")
        if raw:
            try:
                for_date = timezone.datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                pass
        items = list(StockItem.objects.filter(restaurant=restaurant, is_active=True).select_related("base_unit"))
        low = [i for i in items if i.level == "low"]
        out = [i for i in items if i.level == "out"]
        expiring = services.expiring_report(restaurant)
        alerts = list(
            InventoryAlert.objects.filter(restaurant=restaurant, status="open")
            .select_related("stock_item", "menu_item", "modifier", "lot")
            .prefetch_related("platform_tasks__platform", "platform_tasks__done_by")
            .order_by("-created_at")[:50]
        )
        buy = services.buy_list(restaurant, for_date=for_date)
        return {
            "counts": {
                "items": len(items),
                "out": len(out),
                "low": len(low),
                "expiring": len(expiring),
                "alerts": len(alerts),
                "auto_disabled": MenuItem.objects.filter(restaurant=restaurant, auto_disabled_by_stock=True).count(),
            },
            "low_items": sorted(out + low, key=lambda i: (i.level != "out", i.name.lower())),
            "expiring_lots": expiring,
            "buy_lines": buy,
            "buy_total": sum((line.estimated_cost for line in buy), Decimal("0")),
            "for_date": for_date,
            "open_alerts": alerts,
            "platforms": list(RestaurantDeliveryPlatform.objects.filter(restaurant=restaurant, is_enabled=True)),
            "can_manage": has_resource_permission(request, "warehouse", "update"),
            "can_log": has_resource_permission(request, "warehouse_logs", "update"),
            "can_receive": has_resource_permission(request, "warehouse", "create"),
            "currency": restaurant.default_currency,
        }

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [
            path(
                "alerts/<uuid:alert_id>/done/",
                wrap(require_POST(self.alert_done_view)),
                name="inventory_warehouseoverview_alert_done",
            ),
            path(
                "tasks/<uuid:task_id>/toggle/",
                wrap(require_POST(self.task_toggle_view)),
                name="inventory_warehouseoverview_task_toggle",
            ),
            path(
                "lots/<uuid:lot_id>/write-off/",
                wrap(require_POST(self.lot_write_off_view)),
                name="inventory_warehouseoverview_lot_write_off",
            ),
            path("recompute/", wrap(require_POST(self.recompute_view)), name="inventory_warehouseoverview_recompute"),
        ]
        return custom + super().get_urls()

    def _back(self):
        return redirect(f"{self.admin_site.name}:inventory_warehouseoverview_changelist")

    def _guard(self, request, resource, action):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, resource, action):
            raise PermissionDenied

    def alert_done_view(self, request, alert_id):
        self._guard(request, "warehouse", "update")
        alert = InventoryAlert.objects.filter(pk=alert_id, restaurant=request.restaurant).first()
        if alert is None:
            raise PermissionDenied
        alert.resolve(by=request.user)
        messages.success(request, "Alert marked done.")
        return self._back()

    def task_toggle_view(self, request, task_id):
        self._guard(request, "warehouse_logs", "update")
        task = (
            InventoryAlertPlatformTask.objects.select_related("alert", "platform")
            .filter(pk=task_id, alert__restaurant=request.restaurant)
            .first()
        )
        if task is None:
            raise PermissionDenied
        task.toggle(request.user, not task.is_done)
        alert = task.alert
        if alert.is_open and not alert.platform_tasks.filter(is_done=False).exists():
            alert.resolve(by=request.user, reason="tasks")
            messages.success(request, f"All platforms updated -- '{alert.message}' closed.")
        else:
            messages.success(request, f"{task.platform}: {'done' if task.is_done else 'reopened'}.")
        return self._back()

    def lot_write_off_view(self, request, lot_id):
        self._guard(request, "warehouse_logs", "create")
        lot = StockLot.objects.select_related("stock_item").filter(pk=lot_id, restaurant=request.restaurant).first()
        if lot is None:
            raise PermissionDenied
        entry = services.record_expired_lot(lot, by=request.user, staff_member=_membership(request))
        if entry is None:
            messages.info(request, "Nothing left in that lot.")
        else:
            messages.success(request, f"Wrote off {_fmt(entry.quantity, entry.unit)} of {lot.stock_item.name}.")
        return self._back()

    def recompute_view(self, request):
        self._guard(request, "warehouse", "update")
        services.recompute_availability(request.restaurant.pk)
        messages.success(request, "Availability re-checked against current stock.")
        return self._back()


# ── Recipes (inlines on Menu items / Modifiers) & settings ────────────────


class _RecipeLineInline(TenantInlineMixin, TenantForeignKeyScopingMixin, UnfoldTabularInline):
    permission_resource = "warehouse"
    model = RecipeLine
    extra = 0
    fields = ["stock_item", "quantity", "unit", "line_cost", "note"]
    readonly_fields = ["line_cost"]
    autocomplete_fields = ["stock_item"]
    verbose_name = "Ingredient"
    verbose_name_plural = "Recipe (ingredients per portion)"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "stock_item":
            kwargs["queryset"] = StockItem.objects.filter(restaurant=request.restaurant, is_active=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @display(description="Cost")
    def line_cost(self, obj):
        if not obj.pk or not obj.stock_item_id:
            return "—"
        return f"{services.line_cost(obj)}"


class MenuItemRecipeLineInline(_RecipeLineInline):
    fk_name = "menu_item"


class ModifierRecipeLineInline(_RecipeLineInline):
    fk_name = "modifier"


class RecipeAdminMixin:
    """
    Adds the recipe inline to MenuItem / Modifier admins while the warehouse
    is on and re-checks availability after every save.
    """

    recipe_inline = None

    def get_inlines(self, request, obj):
        inlines = list(super().get_inlines(request, obj))
        restaurant = getattr(request, "restaurant", None)
        if restaurant and restaurant.warehouse_enabled and has_resource_permission(request, "warehouse", "read"):
            inlines.append(self.recipe_inline)
        return inlines

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        touched = any(isinstance(fs, forms.BaseInlineFormSet) and fs.model is RecipeLine for fs in formsets)
        if touched:
            hooks.on_recipe_changed(form.instance, by=request.user)

    @display(description="Ingredient cost")
    def ingredient_cost(self, obj):
        cost = services.recipe_cost(obj)
        return "—" if cost is None else f"{cost}"


class RestaurantDeliveryPlatformInline(TenantInlineMixin, UnfoldTabularInline):
    """Which delivery apps the restaurant is on -- sold-out checklists are created per enabled row."""

    permission_resource = "settings"
    permission_actions = {"view": "read", "add": "update", "change": "update", "delete": "update"}
    model = RestaurantDeliveryPlatform
    extra = 0
    max_num = 3
    fields = ["platform", "is_enabled", "store_external_id"]
    verbose_name_plural = "Delivery platforms (Glovo / Wolt / Bolt Food) -- for sold-out checklists"


def register_inventory_admin(site):
    site.register(WarehouseOverview, WarehouseOverviewTenantAdmin)
    site.register(StockItem, StockItemTenantAdmin)
    site.register(StockLot, StockLotTenantAdmin)
    site.register(WasteEntry, WasteEntryTenantAdmin)
    site.register(EmployeeMeal, EmployeeMealTenantAdmin)
    site.register(StockAdjustment, StockAdjustmentTenantAdmin)
    site.register(StockMovement, StockMovementTenantAdmin)
    site.register(InventoryAlert, InventoryAlertTenantAdmin)


__all__ = [
    "register_inventory_admin",
    "MenuItemRecipeLineInline",
    "ModifierRecipeLineInline",
    "RecipeAdminMixin",
    "RestaurantDeliveryPlatformInline",
    "Modifier",
]
