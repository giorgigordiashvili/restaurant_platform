"""
Tenant-specific admin classes for restaurant dashboard.

Registers models with tenant_admin_site using role-based permissions.
Each model is filtered to the current restaurant and permissions are
checked against the user's StaffRole.
"""

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from parler.admin import TranslatableAdmin, TranslatableTabularInline
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import TabularInline as UnfoldTabularInline
from unfold.decorators import display

from apps.core import modules
from apps.core.admin_sites import tenant_admin_site
from apps.core.permission_widgets import PermissionMatrixField, rows_for
from apps.core.tenant_admin_base import (  # noqa: F401 -- re-exported for existing imports
    UNFOLD_INPUT_CLASSES,
    UNFOLD_TEXTAREA_CLASSES,
    ModuleEnabledMixin,
    OptionalTranslationInlineForm,
    TenantForeignKeyScopingMixin,
    TenantInlineMixin,
    TenantLanguageDefaultMixin,
    TenantModelAdmin,
    TenantTranslatableAdmin,
    UnfoldTranslatableModelForm,
    has_resource_permission,
    staff_permissions,
)
from apps.inventory.tenant_admin import (
    MenuItemRecipeLineInline,
    ModifierRecipeLineInline,
    RecipeAdminMixin,
    RestaurantDeliveryPlatformInline,
)
from apps.loyalty.models import LoyaltyCounter, LoyaltyProgram, LoyaltyRedemption

# Import models
from apps.menu.models import MenuCategory, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup
from apps.orders.models import Order, OrderItem, OrderStatusHistory
from apps.promotions.tenant_admin import ComboComponentInline
from apps.reservations.models import Reservation, ReservationBlockedTime, ReservationSettings
from apps.reviews.models import Review, ReviewReport
from apps.staff import services as staff_services
from apps.staff.models import StaffInvitation, StaffMember, StaffRole
from apps.tables.models import Table, TableQRCode, TableSection, TableSession
from apps.tenants.models import Restaurant, RestaurantHours, RestaurantModules
from apps.venues import services as venue_services
from apps.venues.models import VenueShareRequest

# =============================================================================
# Menu Admin
# =============================================================================


class MenuCategoryTenantAdmin(TenantTranslatableAdmin):
    """Admin for menu categories."""

    permission_resource = "menu"
    list_display = ["name", "all_languages_column", "display_order", "is_active", "schedule", "items_count"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        from apps.promotions.models import MenuSchedule

        if "schedule" in form.base_fields:
            form.base_fields["schedule"].queryset = MenuSchedule.objects.filter(
                restaurant=getattr(request, "restaurant", None)
            )
        return form

    list_filter = ["is_active"]
    list_editable = ["display_order", "is_active"]
    search_fields = ["translations__name"]
    ordering = ["display_order"]


class MenuItemModifierGroupInline(TenantInlineMixin, TenantForeignKeyScopingMixin, UnfoldTabularInline):
    """Inline for linking modifier groups to menu items."""

    permission_resource = "menu"
    model = MenuItemModifierGroup
    extra = 1
    ordering = ["display_order"]
    autocomplete_fields = ["modifier_group"]
    verbose_name = "Modifier Group"
    verbose_name_plural = "Modifier Groups (select existing or create new in Modifier Groups menu)"
    fields = ["modifier_group", "display_order"]

    def get_queryset(self, request):
        """Prefetch modifiers for display."""
        return super().get_queryset(request).select_related("modifier_group")


class MenuItemTenantAdmin(RecipeAdminMixin, TenantTranslatableAdmin):
    """Admin for menu items."""

    permission_resource = "menu"
    recipe_inline = MenuItemRecipeLineInline
    list_display = [
        "name",
        "all_languages_column",
        "category",
        "price",
        "is_available",
        "auto_disabled_by_stock",
        "ingredient_cost",
        "is_featured",
        "preparation_station",
    ]
    list_filter = [
        "is_available",
        "auto_disabled_by_stock",
        "is_featured",
        "preparation_station",
        "category",
        "is_vegetarian",
        "is_vegan",
        "is_gluten_free",
    ]
    list_editable = ["is_available", "is_featured"]
    search_fields = ["translations__name", "translations__description"]
    ordering = ["category__display_order", "display_order"]
    autocomplete_fields = ["category"]
    inlines = [MenuItemModifierGroupInline, ComboComponentInline]
    # The warehouse owns this flag; staff only ever see it.
    readonly_fields = ["auto_disabled_by_stock"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        restaurant = getattr(request, "restaurant", None)
        from apps.promotions.models import MenuSchedule

        if "schedule" in form.base_fields:
            form.base_fields["schedule"].queryset = MenuSchedule.objects.filter(restaurant=restaurant)
        return form

    def save_model(self, request, obj, form, change):
        old_price = None
        if change and "price" in form.changed_data:
            old_price = MenuItem.objects.filter(pk=obj.pk).values_list("price", flat=True).first()
        super().save_model(request, obj, form, change)
        if old_price is not None and old_price != obj.price:
            try:
                from apps.audit.services import log_action

                log_action(
                    "price_change",
                    request=request,
                    restaurant=request.restaurant,
                    description=f"{obj}: {old_price} -> {obj.price}",
                    target_model="menuitem",
                    target_id=str(obj.pk),
                    changes={"price": {"from": str(old_price), "to": str(obj.price)}},
                )
            except Exception:  # pragma: no cover
                pass

    def get_queryset(self, request):
        """Ensure category is also filtered."""
        return super().get_queryset(request).select_related("category")


class ModifierInline(TenantInlineMixin, TenantLanguageDefaultMixin, TranslatableTabularInline):
    """Inline for adding modifiers directly within a modifier group."""

    permission_resource = "menu"
    model = Modifier
    form = OptionalTranslationInlineForm
    extra = 3  # Show 3 empty rows for quick adding
    ordering = ["display_order"]
    # Don't specify 'fields' - let parler handle the translated fields (name)
    # Only specify non-translated fields we want to show
    verbose_name = "Option"
    verbose_name_plural = "Options (e.g., Small, Medium, Large or Meat, Cheese, Potato)"

    def get_formset(self, request, obj=None, **kwargs):
        """Apply styling to inline form fields."""
        formset = super().get_formset(request, obj, **kwargs)
        if hasattr(formset, "form") and hasattr(formset.form, "base_fields"):
            for field_name in formset.form.base_fields:
                field = formset.form.base_fields[field_name]
                if hasattr(field.widget, "attrs"):
                    if isinstance(field.widget, forms.Textarea):
                        field.widget.attrs["class"] = (
                            field.widget.attrs.get("class", "") + " " + UNFOLD_TEXTAREA_CLASSES
                        )
                    elif isinstance(field.widget, (forms.TextInput, forms.NumberInput)):
                        field.widget.attrs["class"] = field.widget.attrs.get("class", "") + " " + UNFOLD_INPUT_CLASSES
        return formset


class ModifierGroupTenantAdmin(TenantTranslatableAdmin):
    """Admin for modifier groups with inline modifiers."""

    permission_resource = "menu"
    list_display = [
        "internal_name",
        "name",
        "all_languages_column",
        "selection_type",
        "min_selections",
        "max_selections",
        "is_required",
        "is_active",
        "modifiers_count",
    ]
    list_filter = ["selection_type", "is_required", "is_active"]
    search_fields = ["internal_name", "translations__name"]
    ordering = ["display_order"]
    inlines = [ModifierInline]

    # Translated fields are ordinary form fields to parler, so they have to be
    # listed here like any other -- an explicit fieldsets that leaves them out
    # produces a form with no name at all (the language tabs only switch which
    # translation those fields edit).
    fieldsets = (
        (
            None,
            {
                "fields": ("internal_name", "name", "description"),
                "description": (
                    "Internal name is only for staff -- use it to tell similar groups apart "
                    "(e.g. 'ქათმის ხვეულა - ექსტრა'). Name is what customers see."
                ),
            },
        ),
        (
            "Selection Rules",
            {
                "fields": ("selection_type", "is_required", "min_selections", "max_selections"),
                "description": "Single = radio buttons (pick one), Multiple = checkboxes (pick many)",
            },
        ),
        (
            "Display",
            {
                "fields": ("display_order", "is_active"),
            },
        ),
    )

    @admin.display(description=_("Options"))
    def modifiers_count(self, obj):
        return obj.modifiers.count()


class ModifierTenantAdmin(RecipeAdminMixin, TenantTranslatableAdmin):
    """Admin for modifiers (can also be edited individually)."""

    permission_resource = "menu"
    recipe_inline = ModifierRecipeLineInline
    readonly_fields = ["auto_disabled_by_stock"]
    restaurant_field = None  # Modifier doesn't have direct restaurant FK

    list_display = [
        "name",
        "all_languages_column",
        "group",
        "price_adjustment",
        "is_available",
        "is_default",
    ]
    list_filter = ["is_available", "is_default", "group"]
    list_editable = ["is_available", "is_default"]
    search_fields = ["translations__name"]
    ordering = ["group__display_order", "display_order"]

    def get_queryset(self, request):
        """Filter by restaurant via group."""
        qs = super().get_queryset(request)
        restaurant = getattr(request, "restaurant", None)
        if restaurant:
            qs = qs.filter(group__restaurant=restaurant)
        return qs.select_related("group")


# =============================================================================
# Shared venue (food hall) Admin
# =============================================================================


class VenueInviteForm(forms.Form):
    to_restaurant = forms.SlugField(label="Restaurant slug", help_text="The part before .admin.aimenu.ge")
    venue_name = forms.CharField(label="Venue name", max_length=150, required=False)
    message = forms.CharField(label="Message", widget=forms.Textarea(attrs={"rows": 2}), required=False)


class VenueAcceptForm(forms.Form):
    layout = forms.ChoiceField(
        choices=[
            (venue_services.LAYOUT_OURS, "Use our sections & tables"),
            (venue_services.LAYOUT_THEIRS, "Use theirs"),
        ],
        required=False,
    )
    venue_name = forms.CharField(max_length=150, required=False)


class VenueTableForm(forms.Form):
    number = forms.CharField(max_length=20)
    name = forms.CharField(max_length=100, required=False)
    capacity = forms.IntegerField(min_value=1, initial=4)
    min_capacity = forms.IntegerField(min_value=1, initial=1)
    shape = forms.ChoiceField(
        choices=[("square", "Square"), ("round", "Round"), ("rectangle", "Rectangle")], initial="square"
    )
    section = forms.UUIDField(required=False)


class VenueShareRequestTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    """
    The "Shared venue" page: current venue, its tables, incoming/outgoing
    share requests and the invite form. The stock add/change forms are
    disabled -- they would render selects listing every restaurant on the
    platform. Everything goes through apps.venues.services, same as the REST
    endpoints under /api/v1/dashboard/venue/.
    """

    permission_resource = "settings"
    module_code = "tables"
    restaurant_field = None
    list_display = ["direction_display", "other_restaurant", "venue_name", "status", "created_at"]
    list_filter = ["status"]
    ordering = ["-created_at"]
    change_list_template = "admin/venues/venuesharerequest/change_list.html"

    def get_queryset(self, request):
        restaurant = getattr(request, "restaurant", None)
        qs = super().get_queryset(request).select_related("from_restaurant", "to_restaurant")
        if not restaurant:
            return qs.none()
        return qs.filter(Q(from_restaurant=restaurant) | Q(to_restaurant=restaurant))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def add_view(self, request, form_url="", extra_context=None):
        raise PermissionDenied

    def change_view(self, request, object_id, form_url="", extra_context=None):
        raise PermissionDenied

    @admin.display(description=_("Direction"))
    def direction_display(self, obj):
        return "Incoming" if obj.to_restaurant_id == self._restaurant_id else "Outgoing"

    @admin.display(description=_("Restaurant"))
    def other_restaurant(self, obj):
        return obj.from_restaurant if obj.to_restaurant_id == self._restaurant_id else obj.to_restaurant

    _restaurant_id = None

    def changelist_view(self, request, extra_context=None):
        self._restaurant_id = getattr(request.restaurant, "pk", None)
        extra_context = dict(extra_context or {})
        extra_context.update(self._page_context(request))
        return super().changelist_view(request, extra_context=extra_context)

    def _can(self, request, action):
        return has_resource_permission(request, "tables", action)

    def _page_context(self, request):
        restaurant = request.restaurant
        membership = venue_services.get_membership(restaurant)
        venue = membership.venue if membership and membership.venue.is_active else None
        pending = VenueShareRequest.objects.filter(status=VenueShareRequest.STATUS_PENDING).select_related(
            "from_restaurant", "to_restaurant"
        )
        incoming = [
            {"obj": r, "layout_options": venue_services.layout_options(r)}
            for r in pending.filter(to_restaurant=restaurant)
        ]
        tables, sections, local = [], [], {}
        if venue:
            sections = list(venue.sections.filter(is_active=True))
            tables = list(venue.tables.select_related("section").order_by("section__display_order", "number"))
            local = {t.venue_table_id: t for t in Table.objects.filter(restaurant=restaurant, venue_table__in=tables)}
        return {
            "venue": venue,
            "membership": membership,
            "members": list(venue.active_memberships()) if venue else [],
            "venue_sections": sections,
            "venue_tables": [(t, local.get(t.pk)) for t in tables],
            "incoming_requests": incoming,
            "outgoing_requests": list(pending.filter(from_restaurant=restaurant)),
            "can_manage": self._can(request, "create"),
            "can_leave": self._can(request, "delete") and venue is not None,
            "invite_form": VenueInviteForm(),
            "table_form": VenueTableForm(),
            "layout_ours": venue_services.LAYOUT_OURS,
            "layout_theirs": venue_services.LAYOUT_THEIRS,
        }

    # --- POST-only actions, all redirecting back to this page

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [
            path("invite/", wrap(require_POST(self.invite_view)), name="venues_venuesharerequest_invite"),
            path(
                "<uuid:object_id>/accept/", wrap(require_POST(self.accept_view)), name="venues_venuesharerequest_accept"
            ),
            path(
                "<uuid:object_id>/decline/",
                wrap(require_POST(self.decline_view)),
                name="venues_venuesharerequest_decline",
            ),
            path(
                "<uuid:object_id>/cancel/", wrap(require_POST(self.cancel_view)), name="venues_venuesharerequest_cancel"
            ),
            path("leave/", wrap(require_POST(self.leave_view)), name="venues_venuesharerequest_leave"),
            path("tables/add/", wrap(require_POST(self.table_add_view)), name="venues_venuesharerequest_table_add"),
            path(
                "tables/<uuid:table_id>/deactivate/",
                wrap(require_POST(self.table_deactivate_view)),
                name="venues_venuesharerequest_table_deactivate",
            ),
            path(
                "tables/<uuid:table_id>/regenerate-qr/",
                wrap(require_POST(self.table_regenerate_qr_view)),
                name="venues_venuesharerequest_table_regenerate_qr",
            ),
        ]
        return custom + super().get_urls()

    def _back(self):
        return redirect(f"{self.admin_site.name}:venues_venuesharerequest_changelist")

    def _guard(self, request, action="create"):
        if not getattr(request, "restaurant", None) or not self._can(request, action):
            raise PermissionDenied

    def _fail(self, request, exc):
        messages.error(request, exc.message)
        return self._back()

    def invite_view(self, request):
        self._guard(request)
        form = VenueInviteForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("; ").join(f"{k}: {', '.join(v)}" for k, v in form.errors.items()))
            return self._back()
        try:
            req = venue_services.send_share_request(
                request.restaurant,
                form.cleaned_data["to_restaurant"],
                request.user,
                venue_name=form.cleaned_data["venue_name"],
                message=form.cleaned_data["message"],
            )
        except venue_services.VenueError as exc:
            return self._fail(request, exc)
        messages.success(request, _("Request sent to {p0}.").format(p0=req.to_restaurant.name))
        return self._back()

    def accept_view(self, request, object_id):
        self._guard(request)
        form = VenueAcceptForm(request.POST)
        form.is_valid()
        try:
            venue, summary = venue_services.accept_share_request(
                object_id,
                request.restaurant,
                request.user,
                layout=form.cleaned_data.get("layout") or None,
                venue_name=form.cleaned_data.get("venue_name", ""),
            )
        except venue_services.VenueError as exc:
            return self._fail(request, exc)
        messages.success(
            request,
            _("You now share tables at {p0}: {p1} table(s) added, {p2} linked.").format(
                p0=venue.name, p1=summary.created, p2=summary.linked
            ),
        )
        return self._back()

    def decline_view(self, request, object_id):
        self._guard(request)
        try:
            venue_services.decline_share_request(object_id, request.restaurant, request.user)
        except venue_services.VenueError as exc:
            return self._fail(request, exc)
        messages.success(request, _("Request declined."))
        return self._back()

    def cancel_view(self, request, object_id):
        self._guard(request)
        try:
            venue_services.cancel_share_request(object_id, request.restaurant, request.user)
        except venue_services.VenueError as exc:
            return self._fail(request, exc)
        messages.success(request, _("Request cancelled."))
        return self._back()

    def leave_view(self, request):
        self._guard(request, "delete")
        try:
            venue_services.leave_venue(request.restaurant)
        except venue_services.VenueError as exc:
            return self._fail(request, exc)
        messages.success(request, _("You left the shared venue. Your tables are now your own again."))
        return self._back()

    def table_add_view(self, request):
        self._guard(request)
        membership = venue_services.get_membership(request.restaurant)
        if membership is None:
            raise PermissionDenied
        form = VenueTableForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("; ").join(f"{k}: {', '.join(v)}" for k, v in form.errors.items()))
            return self._back()
        fields = dict(form.cleaned_data)
        section_id = fields.pop("section", None)
        fields["section"] = membership.venue.sections.filter(pk=section_id).first() if section_id else None
        try:
            vt, summary = venue_services.create_venue_table(membership.venue, **fields)
        except venue_services.VenueError as exc:
            return self._fail(request, exc)
        messages.success(
            request, _("Table {p0} added to every restaurant at {p1}.").format(p0=vt.number, p1=membership.venue.name)
        )
        return self._back()

    def table_deactivate_view(self, request, table_id):
        self._guard(request)
        membership = venue_services.get_membership(request.restaurant)
        vt = membership.venue.tables.filter(pk=table_id).first() if membership else None
        if vt is None:
            raise PermissionDenied
        skipped = venue_services.deactivate_venue_table(vt)
        note = f" (kept active at {', '.join(skipped)}: session in progress)" if skipped else ""
        messages.success(request, _("Table {p0} retired{p1}.").format(p0=vt.number, p1=note))
        return self._back()

    def table_regenerate_qr_view(self, request, table_id):
        self._guard(request)
        membership = venue_services.get_membership(request.restaurant)
        vt = membership.venue.tables.filter(pk=table_id).first() if membership else None
        if vt is None:
            raise PermissionDenied
        vt.regenerate_qr_image()
        messages.success(
            request,
            _("QR image for table {p0} regenerated (short link). Download and reprint it.").format(p0=vt.number),
        )
        return self._back()


# =============================================================================
# Orders Admin
# =============================================================================


class OrderItemInline(TenantInlineMixin, UnfoldTabularInline):
    permission_resource = "orders"
    permission_actions = {"view": "read", "add": "read", "change": "read", "delete": "read"}
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ["item_name", "quantity", "unit_price", "total_price", "status", "preparation_station"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class OrderStatusHistoryInline(TenantInlineMixin, UnfoldTabularInline):
    permission_resource = "orders"
    permission_actions = {"view": "read", "add": "read", "change": "read", "delete": "read"}
    model = OrderStatusHistory
    extra = 0
    can_delete = False
    fields = ["from_status", "to_status", "changed_by", "notes", "created_at"]
    readonly_fields = fields
    ordering = ["-created_at"]

    def has_add_permission(self, request, obj=None):
        return False


class OrderTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    """
    Orders, read-only. Status changes happen in the POS / API so the
    warehouse hooks and the status history always run.
    """

    permission_resource = "orders"
    module_code = "ordering"
    list_display = ["order_number", "status", "order_type", "source", "table", "customer_name", "total", "created_at"]
    list_filter = ["status", "order_type", "source", "created_at"]
    search_fields = ["order_number", "customer_name", "customer_phone", "external_id"]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"
    inlines = [OrderItemInline, OrderStatusHistoryInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("table", "customer")

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in Order._meta.fields if f.name != "id"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# =============================================================================
# Tables Admin
# =============================================================================


class SharedRowsFilter(admin.SimpleListFilter):
    title = "shared venue"
    parameter_name = "shared"
    link_field = "venue_table"

    def lookups(self, request, model_admin):
        return [("yes", "Shared (venue)"), ("no", "Own")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(**{f"{self.link_field}__isnull": False})
        if self.value() == "no":
            return queryset.filter(**{f"{self.link_field}__isnull": True})
        return queryset


class SharedSectionsFilter(SharedRowsFilter):
    link_field = "venue_section"


class VenueManagedRowsMixin:
    """
    Rows mirrored from a shared venue: layout fields are read-only here (they
    are edited on the Shared venue page), the venue link never shows, and the
    row cannot be deleted -- leaving the venue unlinks it instead.
    """

    locked_fields = ()
    link_field = ""

    @admin.display(description=_("Shared"))
    def shared_badge(self, obj):
        if getattr(obj, self.link_field + "_id", None):
            return format_html(
                '<span class="bg-primary-100 text-primary-700 dark:bg-primary-500/20 dark:text-primary-400 '
                'inline-flex items-center px-2 py-0.5 rounded-default text-xs font-semibold">Shared (venue)</span>'
            )
        return ""

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None and getattr(obj, self.link_field + "_id", None):
            fields += [f for f in self.locked_fields if f not in fields]
        return fields

    def get_exclude(self, request, obj=None):
        exclude = list(super().get_exclude(request, obj) or [])
        if self.link_field not in exclude:
            exclude.append(self.link_field)
        return exclude

    def has_delete_permission(self, request, obj=None):
        if obj is not None and getattr(obj, self.link_field + "_id", None):
            return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset.filter(**{f"{self.link_field}__isnull": True}))


class TableSectionTenantAdmin(ModuleEnabledMixin, VenueManagedRowsMixin, TenantModelAdmin):
    """Admin for table sections."""

    permission_resource = "tables"
    module_code = "tables"
    link_field = "venue_section"
    locked_fields = ("name",)
    list_display = ["name", "shared_badge", "display_order", "is_active"]
    list_filter = ["is_active", SharedSectionsFilter]
    list_editable = ["display_order", "is_active"]
    search_fields = ["name"]
    ordering = ["display_order"]


class TableTenantAdmin(ModuleEnabledMixin, VenueManagedRowsMixin, TenantModelAdmin):
    """Admin for tables."""

    permission_resource = "tables"
    module_code = "tables"
    link_field = "venue_table"
    locked_fields = ("number", "name", "capacity", "min_capacity", "section", "shape")
    list_display = [
        "number",
        "shared_badge",
        "name",
        "section",
        "capacity",
        "status",
        "is_active",
    ]
    list_filter = ["status", "is_active", "section", "shape", SharedRowsFilter]
    list_editable = ["status", "is_active"]
    search_fields = ["number", "name"]
    ordering = ["section__display_order", "number"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("section", "venue_table")


class TableQRCodeTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    """Admin for table QR codes."""

    permission_resource = "tables"
    module_code = "tables"
    restaurant_field = None  # QR code links through table

    list_display = [
        "table",
        "name",
        "destination",
        "is_active",
        "image_status",
        "qr_preview",
        "download_link",
        "resolves_count",
    ]
    list_filter = ["is_active", "destination"]
    search_fields = ["code", "name", "table__number"]
    readonly_fields = [
        "code",
        "scans_count",
        "last_scanned_at",
        "resolves_count",
        "last_resolved_at",
        "qr_code_display",
        "qr_url_display",
        "resolved_destination",
        "image_status",
    ]
    ordering = ["table__number"]
    actions = ["regenerate_qr_images"]
    fieldsets = (
        (None, {"fields": ("table", "name", "is_active")}),
        (
            "Destination",
            {
                "fields": ("destination", "custom_url", "resolved_destination"),
                "description": (
                    "The printed code never changes; where it goes is decided when it is scanned. "
                    "'Automatic' follows the table (this restaurant's page, or the shared venue page "
                    "when the table is shared). To move a code to another table, just change the table above."
                ),
            },
        ),
        (
            "QR Code",
            {
                "fields": ("qr_code_display", "qr_url_display", "image_status", "code"),
                "description": "The image encodes the short link. Regenerate it before reprinting a legacy image.",
            },
        ),
        (
            "Statistics",
            {
                "fields": ("resolves_count", "last_resolved_at", "scans_count", "last_scanned_at"),
            },
        ),
    )

    @admin.display(description=_("Currently goes to"))
    def resolved_destination(self, obj):
        from apps.tables.qr_links import resolve

        destination = resolve(obj.code)
        if destination is None:
            return "Not resolvable (code, table or restaurant inactive)"
        return format_html(
            '{} → <a href="{}" target="_blank">{}</a>', destination.kind, destination.url, destination.url
        )

    @admin.display(description=_("Image"))
    def image_status(self, obj):
        if not obj.qr_image:
            return "No image yet"
        if obj.image_is_current:
            return format_html('<span class="text-primary-600 font-semibold">Short link ✓</span>')
        return format_html(
            '<span class="text-red-600 font-semibold" title="{}">Legacy — regenerate before reprinting</span>',
            obj.qr_image_url or obj.direct_url(),
        )

    @admin.action(description=_("Regenerate QR image (short link)"))
    def regenerate_qr_images(self, request, queryset):
        count = 0
        for qr in queryset.select_related("table__restaurant"):
            qr.regenerate_qr_image()
            count += 1
        messages.success(
            request,
            _("Regenerated {p0} QR image(s). Download and reprint them to make the codes dynamic.").format(p0=count),
        )

    regenerate_qr_images.allowed_permissions = ("change",)

    def get_queryset(self, request):
        """Filter by restaurant via table."""
        qs = super().get_queryset(request)
        restaurant = getattr(request, "restaurant", None)
        if restaurant:
            qs = qs.filter(table__restaurant=restaurant)
        return qs.select_related("table")

    @admin.display(description=_("QR Code"))
    def qr_code_display(self, obj):
        """Display QR code image in detail view."""
        from django.utils.html import format_html

        if obj.qr_image:
            return format_html(
                '<img src="{}" style="max-width: 200px; max-height: 200px; border: 1px solid #ccc; padding: 10px; background: white;" />'
                '<br><a href="{}" download class="button" style="margin-top: 10px; display: inline-block;">Download QR Code</a>',
                obj.qr_image.url,
                obj.qr_image.url,
            )
        return "QR code will be generated after save"

    @admin.display(description=_("QR URL"))
    def qr_url_display(self, obj):
        """Display the URL encoded in the QR code."""
        from django.utils.html import format_html

        url = obj.get_qr_url()
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.display(description=_("Preview"))
    def qr_preview(self, obj):
        """Small QR preview for list view."""
        from django.utils.html import format_html

        if obj.qr_image:
            return format_html('<img src="{}" style="width: 50px; height: 50px;" />', obj.qr_image.url)
        return "-"

    @admin.display(description=_("Download"))
    def download_link(self, obj):
        """Download link for list view."""
        from django.utils.html import format_html

        if obj.qr_image:
            return format_html('<a href="{}" download>Download</a>', obj.qr_image.url)
        return "-"


class TableSessionTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    """Admin for table sessions."""

    permission_resource = "tables"
    module_code = "tables"
    restaurant_field = None  # Session links through table

    list_display = [
        "table",
        "status",
        "guest_count",
        "started_at",
        "closed_at",
        "duration_minutes",
    ]
    list_filter = ["status", "started_at"]
    search_fields = ["table__number", "invite_code"]
    readonly_fields = ["invite_code", "started_at"]
    ordering = ["-started_at"]

    def get_queryset(self, request):
        """Filter by restaurant via table."""
        qs = super().get_queryset(request)
        restaurant = getattr(request, "restaurant", None)
        if restaurant:
            qs = qs.filter(table__restaurant=restaurant)
        return qs.select_related("table", "host")


# =============================================================================
# Staff Admin
# =============================================================================


class StaffRoleForm(forms.ModelForm):
    class Meta:
        model = StaffRole
        fields = ["name", "display_name", "description", "permissions"]

    restaurant = None  # set by the admin; the FK is not on the form

    def clean(self):
        data = super().clean()
        name = data.get("name") or getattr(self.instance, "name", None) or "custom"
        display_name = (data.get("display_name") or "").strip()
        if name == "custom":
            if not display_name:
                self.add_error("display_name", "Give the custom role a name.")
            elif self.restaurant is not None:
                # The DB constraint involves the (excluded) restaurant FK, so
                # Django's form validation skips it -- check here instead.
                clash = StaffRole.objects.filter(restaurant=self.restaurant, name="custom", display_name=display_name)
                if self.instance.pk:
                    clash = clash.exclude(pk=self.instance.pk)
                if clash.exists():
                    self.add_error("display_name", "A role with this name already exists.")
        return data


class StaffRoleTenantAdmin(TenantModelAdmin):
    """
    Roles: the built-in ones can be re-tuned, and any number of custom roles
    can be added. Permissions are a checkbox grid over the modules that are
    switched on; permissions of switched-off modules are kept untouched.
    """

    permission_resource = "staff"
    form = StaffRoleForm
    list_display = ["role_label", "name", "is_system_role", "members_count"]
    list_filter = ["is_system_role"]
    search_fields = ["name", "display_name"]
    ordering = ["-is_system_role", "name", "display_name"]
    fields = ["name", "display_name", "description", "permissions"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.restaurant = request.restaurant
        rows = rows_for(request.restaurant)
        visible = {r for r, _ in rows}
        current = obj.permissions if obj else {}
        keep = {k: v for k, v in current.items() if k not in visible}
        form.base_fields["permissions"] = PermissionMatrixField(rows, keep=keep, label="Permissions")
        if "name" in form.base_fields and (obj is None or not obj.is_system_role):
            form.base_fields["name"].choices = [("custom", "Custom role")]
            form.base_fields["name"].initial = "custom"
        return form

    def get_readonly_fields(self, request, obj=None):
        return ["name"] if obj is not None and obj.is_system_role else []

    def has_delete_permission(self, request, obj=None):
        if obj is not None and (obj.is_system_role or obj.members.exists()):
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.name = "custom"
            obj.is_system_role = False
        super().save_model(request, obj, form, change)

    @display(description=_("Role"))
    def role_label(self, obj):
        return obj.get_display_name()

    @display(description=_("Members"))
    def members_count(self, obj):
        return obj.members.filter(is_active=True).count()


class StaffMemberTenantAdmin(TenantModelAdmin):
    """Members are added by invitation; here a manager changes role, access and extra permissions."""

    permission_resource = "staff"
    list_display = ["email", "full_name", "role", "is_active", "last_login"]
    list_filter = ["role", "is_active"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]
    ordering = ["-created_at"]
    fields = [
        "user_display",
        "role",
        "is_active",
        "hourly_rate",
        "permissions_override",
        "notes",
        "joined_at",
        "invited_by",
    ]
    readonly_fields = ["user_display", "joined_at", "invited_by"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user", "role")

    def has_add_permission(self, request):
        return False  # invitations only

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        rows = rows_for(request.restaurant)
        visible = {r for r, _ in rows}
        current = obj.permissions_override if obj else {}
        keep = {k: v for k, v in current.items() if k not in visible}
        form.base_fields["permissions_override"] = PermissionMatrixField(
            rows,
            keep=keep,
            label="Extra permissions",
            help_text="Added on top of the role for this person only.",
        )
        return form

    def save_model(self, request, obj, form, change):
        if not obj.is_active and obj.user_id in (request.restaurant.owner_id, request.user.pk):
            messages.error(request, _("The owner and your own account cannot be deactivated here."))
            obj.is_active = True
        super().save_model(request, obj, form, change)

    @display(description=_("Email"))
    def email(self, obj):
        return obj.user.email

    @display(description=_("Name"))
    def full_name(self, obj):
        return obj.user.full_name

    @display(description=_("Last login"))
    def last_login(self, obj):
        return obj.user.last_login

    @display(description=_("Person"))
    def user_display(self, obj):
        return f"{obj.user.full_name} <{obj.user.email}>"


class StaffInviteAdminForm(forms.ModelForm):
    class Meta:
        model = StaffInvitation
        fields = ["email", "role", "message"]


class StaffInvitationTenantAdmin(TenantModelAdmin):
    """Invite by email; the invitation is emailed on save and immutable afterwards."""

    permission_resource = "staff"
    form = StaffInviteAdminForm
    list_display = ["email", "role", "status_badge", "invited_by", "expires_at", "created_at"]
    list_filter = ["status", "role"]
    search_fields = ["email"]
    ordering = ["-created_at"]
    actions = ["resend_invitations", "cancel_invitations"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("role", "invited_by")

    def get_fields(self, request, obj=None):
        if obj is None:
            return ["email", "role", "message"]
        return ["email", "role", "message", "status", "invited_by", "expires_at", "accepted_at", "accepted_by"]

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return []
        return ["email", "role", "message", "status", "invited_by", "expires_at", "accepted_at", "accepted_by"]

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        try:
            staff_services.invite(
                request.restaurant,
                obj.email,
                obj.role,
                invited_by=request.user,
                message=obj.message,
                instance=obj,
            )
        except staff_services.InviteError as exc:
            messages.error(request, str(exc))
            raise PermissionDenied(str(exc))

    def response_add(self, request, obj, post_url_continue=None):
        messages.success(request, _("Invitation emailed to {p0}.").format(p0=obj.email))
        return redirect("tenant_admin:staff_staffinvitation_changelist")

    @display(
        description=_("Status"),
        label={"pending": "info", "accepted": "success", "expired": "warning", "cancelled": "danger"},
    )
    def status_badge(self, obj):
        return "expired" if obj.status == "pending" and obj.is_expired else obj.status

    @admin.action(description=_("Resend invitation email"))
    def resend_invitations(self, request, queryset):
        if not has_resource_permission(request, "staff", "create"):
            raise PermissionDenied
        n = 0
        for inv in queryset.filter(status="pending"):
            staff_services.resend(inv, by=request.user)
            n += 1
        self.message_user(request, f"{n} invitation(s) resent.")

    @admin.action(description=_("Cancel invitations"))
    def cancel_invitations(self, request, queryset):
        if not has_resource_permission(request, "staff", "delete"):
            raise PermissionDenied
        n = 0
        for inv in queryset.filter(status="pending"):
            staff_services.cancel(inv, by=request.user)
            n += 1
        self.message_user(request, f"{n} invitation(s) cancelled.")


# =============================================================================
# Reservations Admin
# =============================================================================


class ReservationTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    """Reservations: read-mostly; status moves through the actions below."""

    permission_resource = "reservations"
    module_code = "reservations"
    list_display = [
        "confirmation_code",
        "guest_name",
        "reservation_date",
        "reservation_time",
        "party_size",
        "table",
        "status",
    ]
    list_filter = ["status", "source", "reservation_date"]
    search_fields = ["confirmation_code", "guest_name", "guest_email", "guest_phone"]
    readonly_fields = ["confirmation_code", "status", "created_at", "updated_at"]
    ordering = ["reservation_date", "reservation_time"]
    date_hierarchy = "reservation_date"
    actions = [
        "confirm_reservations",
        "seat_reservations",
        "complete_reservations",
        "no_show_reservations",
        "cancel_reservations",
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("table", "customer")

    def has_add_permission(self, request):
        return False

    def _transition(self, request, queryset, statuses, method, label, **kwargs):
        if not has_resource_permission(request, "reservations", "update"):
            raise PermissionDenied
        n = 0
        for reservation in queryset.filter(status__in=statuses):
            getattr(reservation, method)(**kwargs)
            n += 1
        self.message_user(request, f"{n} reservation(s) {label}.")

    @admin.action(description=_("Confirm"))
    def confirm_reservations(self, request, queryset):
        self._transition(request, queryset, ["pending", "waitlist"], "confirm", "confirmed", confirmed_by=request.user)

    @admin.action(description=_("Mark seated"))
    def seat_reservations(self, request, queryset):
        self._transition(request, queryset, ["pending", "confirmed"], "mark_seated", "seated")

    @admin.action(description=_("Mark completed"))
    def complete_reservations(self, request, queryset):
        self._transition(request, queryset, ["seated", "confirmed"], "mark_completed", "completed")

    @admin.action(description=_("Mark no-show"))
    def no_show_reservations(self, request, queryset):
        self._transition(request, queryset, ["pending", "confirmed"], "mark_no_show", "marked no-show")

    @admin.action(description=_("Cancel"))
    def cancel_reservations(self, request, queryset):
        self._transition(
            request,
            queryset,
            ["pending", "confirmed", "waitlist"],
            "cancel",
            "cancelled",
            cancelled_by=request.user,
            reason="Cancelled by staff",
        )


class ReservationSettingsTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    """Admin for reservation settings."""

    permission_resource = "reservations"
    module_code = "reservations"
    list_display = ["min_party_size", "max_party_size", "advance_booking_days", "require_confirmation"]
    # The on/off switch is the Reservations module (Settings -> Modules).
    exclude = ["accepts_reservations"]

    def has_add_permission(self, request):
        """Only one settings object per restaurant."""
        restaurant = getattr(request, "restaurant", None)
        if restaurant and ReservationSettings.objects.filter(restaurant=restaurant).exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        """Settings should not be deleted."""
        return False


class ReservationBlockedTimeTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    """Admin for blocked reservation times."""

    permission_resource = "reservations"
    module_code = "reservations"
    list_display = [
        "start_datetime",
        "end_datetime",
        "reason",
        "is_all_tables",
        "is_active",
    ]
    list_filter = ["reason", "start_datetime"]
    search_fields = ["description"]
    ordering = ["start_datetime"]


# =============================================================================
# Restaurant Settings Admin
# =============================================================================


class RestaurantHoursInline(TenantInlineMixin, UnfoldTabularInline):
    """Inline for restaurant operating hours."""

    model = RestaurantHours
    extra = 7  # preload a row per weekday so new restaurants aren't empty
    max_num = 7
    ordering = ["day_of_week"]

    # Hours are part of the restaurant's settings: whoever may update settings
    # may add, change or remove hour rows.
    permission_resource = "settings"
    permission_actions = {"view": "read", "add": "update", "change": "update", "delete": "update"}


class RestaurantSettingsAdmin(UnfoldModelAdmin):
    """
    Admin for editing the current restaurant's settings.

    Only shows the current restaurant - no list view needed. Module switches
    (ordering, reservations, warehouse, payments...) live on the Modules page.
    """

    list_display = ["name", "is_active", "default_currency", "timezone"]
    readonly_fields = ["slug", "owner", "average_rating", "total_reviews", "total_orders", "created_at", "updated_at"]
    inlines = [RestaurantHoursInline]
    filter_horizontal = ["amenities"]

    fieldsets = (
        (
            "Basic Info",
            {
                "fields": ("name", "slug", "description", "category", "is_active"),
                "description": (
                    "Which features this restaurant uses (ordering, reservations, warehouse, "
                    "payments...) is set under Settings -> Modules."
                ),
            },
        ),
        ("Amenities", {"fields": ("amenities",)}),
        ("Contact", {"fields": ("email", "phone", "website")}),
        ("Address", {"fields": ("address", "city", "postal_code", "country", "latitude", "longitude")}),
        ("Branding", {"fields": ("logo", "cover_image", "primary_color", "secondary_color")}),
        ("Settings", {"fields": ("default_currency", "timezone", "default_language")}),
        (
            "Orders & Pricing",
            {"fields": ("service_charge", "minimum_order_amount", "average_preparation_time")},
        ),
        (
            "Statistics (read-only)",
            {"fields": ("average_rating", "total_reviews", "total_orders"), "classes": ("collapse",)},
        ),
        ("System", {"fields": ("owner", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_inlines(self, request, obj):
        inlines = list(super().get_inlines(request, obj))
        if obj is not None and (modules.is_enabled(obj, "warehouse") or modules.is_enabled(obj, "delivery")):
            inlines.append(RestaurantDeliveryPlatformInline)
        return inlines

    def get_queryset(self, request):
        """Only show the current restaurant."""
        qs = super().get_queryset(request)
        restaurant = getattr(request, "restaurant", None)
        if restaurant:
            return qs.filter(pk=restaurant.pk)
        return qs.none()

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    # Role-based like every other page: a custom role granted settings
    # read/update works the same as the built-in manager.
    def has_module_permission(self, request):
        return bool(getattr(request, "restaurant", None)) and has_resource_permission(request, "settings", "read")

    def has_view_permission(self, request, obj=None):
        return bool(getattr(request, "restaurant", None)) and has_resource_permission(request, "settings", "read")

    def has_change_permission(self, request, obj=None):
        return bool(getattr(request, "restaurant", None)) and has_resource_permission(request, "settings", "update")


class ModulesTenantAdmin(TenantModelAdmin):
    """
    The Modules page: one card per product area with an on/off switch and
    the module's options. Every change goes through apps.core.modules so
    dependency rules, hooks and the audit trail apply.
    """

    permission_resource = "settings"
    restaurant_field = None
    change_list_template = "admin/tenants/restaurantmodules/change_list.html"
    list_display = ["name"]

    def get_queryset(self, request):
        restaurant = getattr(request, "restaurant", None)
        qs = super().get_queryset(request)
        return qs.filter(pk=restaurant.pk) if restaurant else qs.none()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        raise PermissionDenied

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context.update(self._page_context(request))
        return super().changelist_view(request, extra_context=extra_context)

    def _page_context(self, request):
        restaurant = request.restaurant
        cards = []
        for m in modules.MODULES:
            on = modules.is_enabled(restaurant, m.code)
            options = []
            for name in (*m.sub_flags, *m.sub_fields):
                field = restaurant._meta.get_field(name)
                options.append(
                    {
                        "name": name,
                        "label": str(field.verbose_name).capitalize(),
                        "help": str(field.help_text),
                        "is_bool": name in m.sub_flags,
                        "value": getattr(restaurant, name),
                    }
                )
            cards.append(
                {
                    "module": m,
                    "enabled": on,
                    "blockers": modules.check_dependencies(restaurant, m.code, not on) if m.switchable else [],
                    "warnings": modules.warnings(restaurant, m.code) if (on or not m.switchable) else [],
                    "options": options,
                    "toggle_url": (
                        reverse(
                            f"tenant_admin:tenants_restaurantmodules_{'disable' if on else 'enable'}", args=[m.code]
                        )
                        if m.switchable
                        else None
                    ),
                    "options_url": (
                        reverse("tenant_admin:tenants_restaurantmodules_options", args=[m.code]) if options else None
                    ),
                }
            )
        return {"cards": cards, "can_manage": has_resource_permission(request, "settings", "update")}

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [
            path("<slug:code>/enable/", wrap(require_POST(self.enable_view)), name="tenants_restaurantmodules_enable"),
            path(
                "<slug:code>/disable/",
                wrap(require_POST(self.disable_view)),
                name="tenants_restaurantmodules_disable",
            ),
            path(
                "<slug:code>/options/",
                wrap(require_POST(self.options_view)),
                name="tenants_restaurantmodules_options",
            ),
        ]
        return custom + super().get_urls()

    def _back(self):
        return redirect("tenant_admin:tenants_restaurantmodules_changelist")

    def _guard(self, request, code):
        if (
            not getattr(request, "restaurant", None)
            or code not in modules.MODULES_BY_CODE
            or not has_resource_permission(request, "settings", "update")
        ):
            raise PermissionDenied

    def _toggle(self, request, code, enabled):
        self._guard(request, code)
        title = modules.MODULES_BY_CODE[code].title
        try:
            modules.set_module(request.restaurant, code, enabled, by=request.user)
            messages.success(request, f"{title} {'switched on' if enabled else 'switched off'}.")
        except modules.ModuleError as exc:
            messages.error(request, _(" ").join(exc.messages))
        return self._back()

    def enable_view(self, request, code):
        return self._toggle(request, code, True)

    def disable_view(self, request, code):
        return self._toggle(request, code, False)

    def options_view(self, request, code):
        self._guard(request, code)
        m = modules.MODULES_BY_CODE[code]
        data = {name: bool(request.POST.get(name)) for name in m.sub_flags}
        data.update({name: request.POST.get(name, "").strip() for name in m.sub_fields})
        try:
            modules.set_options(request.restaurant, code, data, by=request.user)
            messages.success(request, _("{p0} options saved.").format(p0=m.title))
        except DjangoValidationError as exc:
            messages.error(request, _("; ").join(exc.messages))
        return self._back()


# =============================================================================
# Register all models with tenant_admin_site
# =============================================================================

from apps.audit.tenant_admin import register_audit_admin  # noqa: E402
from apps.crm.tenant_admin import register_crm_admin  # noqa: E402
from apps.delivery.tenant_admin import register_delivery_admin  # noqa: E402
from apps.fiscal.tenant_admin import register_fiscal_admin  # noqa: E402
from apps.inventory.tenant_admin import register_inventory_admin  # noqa: E402
from apps.notifications.tenant_admin import register_notifications_admin  # noqa: E402
from apps.ordering.tenant_admin import register_ordering_admin  # noqa: E402
from apps.payments.tenant_admin import register_payments_admin  # noqa: E402
from apps.printing.tenant_admin import register_printing_admin  # noqa: E402
from apps.promotions.tenant_admin import register_promotions_admin  # noqa: E402
from apps.purchasing.tenant_admin import register_purchasing_admin  # noqa: E402
from apps.reports.tenant_admin import register_reports_admin  # noqa: E402
from apps.terminals.tenant_admin import register_terminals_admin  # noqa: E402
from apps.timekeeping.tenant_admin import register_timekeeping_admin  # noqa: E402

register_inventory_admin(tenant_admin_site)
register_payments_admin(tenant_admin_site)
register_reports_admin(tenant_admin_site)
register_printing_admin(tenant_admin_site)
register_fiscal_admin(tenant_admin_site)
register_delivery_admin(tenant_admin_site)
register_notifications_admin(tenant_admin_site)
register_promotions_admin(tenant_admin_site)
register_purchasing_admin(tenant_admin_site)
register_timekeeping_admin(tenant_admin_site)
register_audit_admin(tenant_admin_site)
register_crm_admin(tenant_admin_site)
register_ordering_admin(tenant_admin_site)
register_terminals_admin(tenant_admin_site)

# Restaurant Settings + Modules
tenant_admin_site.register(Restaurant, RestaurantSettingsAdmin)
tenant_admin_site.register(RestaurantModules, ModulesTenantAdmin)

# Menu
tenant_admin_site.register(MenuCategory, MenuCategoryTenantAdmin)
tenant_admin_site.register(MenuItem, MenuItemTenantAdmin)
tenant_admin_site.register(ModifierGroup, ModifierGroupTenantAdmin)
tenant_admin_site.register(Modifier, ModifierTenantAdmin)

# Orders (visible while the Ordering module is on)
tenant_admin_site.register(Order, OrderTenantAdmin)

# Tables
tenant_admin_site.register(TableSection, TableSectionTenantAdmin)
tenant_admin_site.register(Table, TableTenantAdmin)
tenant_admin_site.register(TableQRCode, TableQRCodeTenantAdmin)
tenant_admin_site.register(VenueShareRequest, VenueShareRequestTenantAdmin)
tenant_admin_site.register(TableSession, TableSessionTenantAdmin)

# Staff
tenant_admin_site.register(StaffRole, StaffRoleTenantAdmin)
tenant_admin_site.register(StaffMember, StaffMemberTenantAdmin)
tenant_admin_site.register(StaffInvitation, StaffInvitationTenantAdmin)

# Reservations (visible while the Reservations module is on)
tenant_admin_site.register(Reservation, ReservationTenantAdmin)
tenant_admin_site.register(ReservationSettings, ReservationSettingsTenantAdmin)
tenant_admin_site.register(ReservationBlockedTime, ReservationBlockedTimeTenantAdmin)


# Loyalty
class LoyaltyProgramTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    permission_resource = "menu"  # reuses the menu-manager role bucket
    module_code = "loyalty"
    restaurant_field = "restaurant"
    list_display = [
        "name",
        "trigger_item",
        "threshold",
        "reward_item",
        "reward_quantity",
        "is_active",
    ]
    list_filter = ["is_active"]
    search_fields = ["name"]
    autocomplete_fields = ["trigger_item", "reward_item"]
    fields = [
        "name",
        "description",
        "is_active",
        "trigger_item",
        "threshold",
        "reward_item",
        "reward_quantity",
        "starts_at",
        "ends_at",
        "code_ttl_seconds",
    ]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        restaurant = getattr(request, "restaurant", None)
        if restaurant and db_field.name in ("trigger_item", "reward_item"):
            kwargs["queryset"] = db_field.related_model.objects.filter(restaurant=restaurant)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        # Explicit auto-assign so failures are visible; the base class does
        # the same but is defensive about missing fields.
        if obj.restaurant_id is None:
            restaurant = getattr(request, "restaurant", None)
            if restaurant is None:
                raise ValueError("Tenant admin has no restaurant context — middleware didn't resolve a subdomain.")
            obj.restaurant = restaurant
        super().save_model(request, obj, form, change)


class LoyaltyCounterTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    permission_resource = "menu"
    module_code = "loyalty"
    restaurant_field = "program__restaurant"
    list_display = ["program", "user", "phone_number", "punches", "last_earned_at"]
    list_filter = ["program"]
    search_fields = ["user__email", "phone_number"]
    autocomplete_fields = ["program"]
    readonly_fields = ["punches", "last_earned_at"]


class LoyaltyRedemptionTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    permission_resource = "menu"
    module_code = "loyalty"
    restaurant_field = "program__restaurant"
    list_display = [
        "code",
        "program",
        "user",
        "status",
        "issued_at",
        "expires_at",
        "redeemed_at",
    ]
    list_filter = ["status", "program"]
    search_fields = ["code", "user__email", "phone_number"]
    readonly_fields = [
        "code",
        "issued_at",
        "expires_at",
        "redeemed_at",
        "redeemed_by",
        "counter",
    ]


tenant_admin_site.register(LoyaltyProgram, LoyaltyProgramTenantAdmin)
tenant_admin_site.register(LoyaltyCounter, LoyaltyCounterTenantAdmin)
tenant_admin_site.register(LoyaltyRedemption, LoyaltyRedemptionTenantAdmin)


# Reviews — owners / managers can browse reviews and flag ones they
# want platform admins to look at. Everything is read-only; the Report
# action files a ReviewReport row against the current user.
class ReviewTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    permission_resource = "menu"  # managers who edit the menu can browse reviews
    module_code = "reviews"
    restaurant_field = "restaurant"

    list_display = [
        "rating",
        "short_title",
        "user",
        "is_hidden",
        "open_reports",
        "created_at",
    ]
    list_filter = ["rating", "is_hidden"]
    search_fields = ["title", "body", "user__email"]
    readonly_fields = [
        "order",
        "restaurant",
        "user",
        "rating",
        "title",
        "body",
        "is_hidden",
        "edited_at",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "created_at"
    actions = ["report_reviews"]

    def short_title(self, obj):
        return obj.title[:40] or obj.body[:40]

    short_title.short_description = "Title"

    def open_reports(self, obj):
        return obj.reports.filter(resolution=ReviewReport.RESOLUTION_NONE).count()

    open_reports.short_description = "Open reports"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        # Everything on this admin is read-only; the Report action creates
        # new ReviewReport rows without mutating the Review itself.
        # The mixin must still win when the Reviews module is off.
        return self._module_on(request) and self._has_resource_permission(request, "read")

    @admin.action(description=_("Report selected reviews to platform moderators"))
    def report_reviews(self, request, queryset):
        from django.contrib import messages

        from apps.reviews.models import ReviewReport

        made = 0
        skipped = 0
        for review in queryset:
            _report, created = ReviewReport.objects.get_or_create(
                review=review,
                reporter=request.user,
                defaults={"reason": ReviewReport.REASON_OTHER, "notes": "Reported via tenant admin."},
            )
            if created:
                made += 1
            else:
                skipped += 1
        msg = f"Reported {made} review(s)."
        if skipped:
            msg += f" Skipped {skipped} already reported."
        self.message_user(request, msg, level=messages.SUCCESS)


tenant_admin_site.register(Review, ReviewTenantAdmin)
