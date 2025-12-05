"""
Tenant-specific admin classes for restaurant dashboard.

Registers models with tenant_admin_site using role-based permissions.
Each model is filtered to the current restaurant and permissions are
checked against the user's StaffRole.
"""

from django import forms
from django.contrib import admin
from parler.admin import TranslatableAdmin, TranslatableTabularInline
from parler.forms import TranslatableModelForm
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import TabularInline as UnfoldTabularInline

from apps.core.admin_sites import tenant_admin_site

# Unfold input styling classes
UNFOLD_INPUT_CLASSES = (
    "border border-base-200 bg-white font-medium min-w-20 placeholder-base-400 "
    "rounded-default shadow-xs text-font-default-light text-sm focus:outline-2 "
    "focus:-outline-offset-2 focus:outline-primary-600 group-[.errors]:border-red-600 "
    "focus:group-[.errors]:outline-red-600 dark:bg-base-900 dark:border-base-700 "
    "dark:text-font-default-dark dark:group-[.errors]:border-red-500 "
    "dark:focus:group-[.errors]:outline-red-500 dark:scheme-dark "
    "group-[.primary]:border-transparent disabled:!bg-base-50 "
    "dark:disabled:!bg-base-800 px-3 py-2 w-full max-w-2xl"
)

UNFOLD_TEXTAREA_CLASSES = UNFOLD_INPUT_CLASSES + " min-h-[120px]"


class UnfoldTranslatableModelForm(TranslatableModelForm):
    """Translatable form with Unfold styling applied to all fields."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "")
                field.widget.attrs["class"] += " " + UNFOLD_TEXTAREA_CLASSES
            elif isinstance(field.widget, (forms.TextInput, forms.NumberInput, forms.EmailInput)):
                field.widget.attrs.setdefault("class", "")
                field.widget.attrs["class"] += " " + UNFOLD_INPUT_CLASSES

# Import models
from apps.menu.models import MenuCategory, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup
from apps.orders.models import Order, OrderItem, OrderStatusHistory
from apps.reservations.models import (
    Reservation,
    ReservationBlockedTime,
    ReservationSettings,
)
from apps.staff.models import StaffInvitation, StaffMember, StaffRole
from apps.tables.models import Table, TableQRCode, TableSection, TableSession
from apps.tenants.models import Amenity, Restaurant, RestaurantCategory, RestaurantHours


class TenantModelAdmin(UnfoldModelAdmin):
    """
    Base admin class for tenant-scoped models.

    Provides:
    - Automatic filtering to current restaurant
    - Role-based permission checking
    - Restaurant auto-assignment on save
    """

    # Override in subclass: "menu", "orders", "tables", "staff", "reservations"
    permission_resource = None

    # Field name for restaurant FK (override if different)
    restaurant_field = "restaurant"

    def get_queryset(self, request):
        """Filter queryset to current restaurant only."""
        qs = super().get_queryset(request)
        restaurant = getattr(request, "restaurant", None)

        if restaurant and self.restaurant_field:
            filter_kwargs = {self.restaurant_field: restaurant}
            qs = qs.filter(**filter_kwargs)

        return qs

    def save_model(self, request, obj, form, change):
        """Auto-set restaurant on new objects."""
        if not change and self.restaurant_field:
            restaurant = getattr(request, "restaurant", None)
            if restaurant and hasattr(obj, self.restaurant_field):
                setattr(obj, self.restaurant_field, restaurant)
        super().save_model(request, obj, form, change)

    def _get_staff_permissions(self, request):
        """Get the current user's staff permissions for this restaurant."""
        restaurant = getattr(request, "restaurant", None)
        if not restaurant:
            return {}

        if request.user.is_superuser:
            return {"*": ["create", "read", "update", "delete"]}

        try:
            staff = request.user.staff_memberships.get(
                restaurant=restaurant, is_active=True
            )
            return staff.get_effective_permissions()
        except Exception:
            return {}

    def _has_resource_permission(self, request, action):
        """Check if user has permission for the given action on this resource."""
        if not self.permission_resource:
            return request.user.is_superuser

        permissions = self._get_staff_permissions(request)

        # Superuser has all permissions
        if "*" in permissions:
            return True

        resource_perms = permissions.get(self.permission_resource, [])
        return action in resource_perms or "*" in resource_perms

    def has_view_permission(self, request, obj=None):
        """Check read permission."""
        return self._has_resource_permission(request, "read")

    def has_add_permission(self, request):
        """Check create permission."""
        return self._has_resource_permission(request, "create")

    def has_change_permission(self, request, obj=None):
        """Check update permission."""
        return self._has_resource_permission(request, "update")

    def has_delete_permission(self, request, obj=None):
        """Check delete permission."""
        return self._has_resource_permission(request, "delete")

    def has_module_permission(self, request):
        """Check if user can see this model in admin index."""
        return self._has_resource_permission(request, "read")


class TenantTranslatableAdmin(TranslatableAdmin, TenantModelAdmin):
    """
    Combined admin for translatable models with tenant scoping.

    Use this for models that use django-parler for translations.
    """

    def get_form(self, request, obj=None, **kwargs):
        """Apply Unfold styling to translatable form fields."""
        form = super().get_form(request, obj, **kwargs)

        # Apply Unfold classes to all form fields
        for field_name, field in form.base_fields.items():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "")
                field.widget.attrs["class"] += " " + UNFOLD_TEXTAREA_CLASSES
            elif isinstance(field.widget, (forms.TextInput, forms.NumberInput, forms.EmailInput)):
                field.widget.attrs.setdefault("class", "")
                field.widget.attrs["class"] += " " + UNFOLD_INPUT_CLASSES

        return form


# =============================================================================
# Menu Admin
# =============================================================================


class MenuCategoryTenantAdmin(TenantTranslatableAdmin):
    """Admin for menu categories."""

    permission_resource = "menu"
    list_display = ["name", "display_order", "is_active", "items_count"]
    list_filter = ["is_active"]
    list_editable = ["display_order", "is_active"]
    search_fields = ["translations__name"]
    ordering = ["display_order"]


class MenuItemModifierGroupInline(UnfoldTabularInline):
    """Inline for linking modifier groups to menu items."""

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


class MenuItemTenantAdmin(TenantTranslatableAdmin):
    """Admin for menu items."""

    permission_resource = "menu"
    list_display = [
        "name",
        "category",
        "price",
        "is_available",
        "is_featured",
        "preparation_station",
    ]
    list_filter = [
        "is_available",
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
    inlines = [MenuItemModifierGroupInline]

    def get_queryset(self, request):
        """Ensure category is also filtered."""
        return super().get_queryset(request).select_related("category")


class ModifierInline(TranslatableTabularInline):
    """Inline for adding modifiers directly within a modifier group."""

    model = Modifier
    extra = 3  # Show 3 empty rows for quick adding
    ordering = ["display_order"]
    # Don't specify 'fields' - let parler handle the translated fields (name)
    # Only specify non-translated fields we want to show
    verbose_name = "Option"
    verbose_name_plural = "Options (e.g., Small, Medium, Large or Meat, Cheese, Potato)"

    def get_formset(self, request, obj=None, **kwargs):
        """Apply styling to inline form fields."""
        formset = super().get_formset(request, obj, **kwargs)
        if hasattr(formset, 'form') and hasattr(formset.form, 'base_fields'):
            for field_name in formset.form.base_fields:
                field = formset.form.base_fields[field_name]
                if hasattr(field.widget, 'attrs'):
                    if isinstance(field.widget, forms.Textarea):
                        field.widget.attrs['class'] = field.widget.attrs.get('class', '') + ' ' + UNFOLD_TEXTAREA_CLASSES
                    elif isinstance(field.widget, (forms.TextInput, forms.NumberInput)):
                        field.widget.attrs['class'] = field.widget.attrs.get('class', '') + ' ' + UNFOLD_INPUT_CLASSES
        return formset


class ModifierGroupTenantAdmin(TenantTranslatableAdmin):
    """Admin for modifier groups with inline modifiers."""

    permission_resource = "menu"
    list_display = [
        "name",
        "selection_type",
        "min_selections",
        "max_selections",
        "is_required",
        "is_active",
        "modifiers_count",
    ]
    list_filter = ["selection_type", "is_required", "is_active"]
    search_fields = ["translations__name"]
    ordering = ["display_order"]
    inlines = [ModifierInline]

    # Note: name and description are translated fields handled by parler
    # They will appear in the language tabs automatically
    fieldsets = (
        ("Selection Rules", {
            "fields": ("selection_type", "is_required", "min_selections", "max_selections"),
            "description": "Single = radio buttons (pick one), Multiple = checkboxes (pick many)",
        }),
        ("Display", {
            "fields": ("display_order", "is_active"),
        }),
    )

    @admin.display(description="Options")
    def modifiers_count(self, obj):
        return obj.modifiers.count()


class ModifierTenantAdmin(TenantTranslatableAdmin):
    """Admin for modifiers (can also be edited individually)."""

    permission_resource = "menu"
    restaurant_field = None  # Modifier doesn't have direct restaurant FK

    list_display = [
        "name",
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
# Orders Admin
# =============================================================================


class OrderTenantAdmin(TenantModelAdmin):
    """Admin for orders."""

    permission_resource = "orders"
    list_display = [
        "order_number",
        "status",
        "order_type",
        "table",
        "total",
        "created_at",
    ]
    list_filter = ["status", "order_type", "created_at"]
    search_fields = ["order_number", "customer_name", "customer_phone"]
    readonly_fields = [
        "order_number",
        "subtotal",
        "tax_amount",
        "service_charge",
        "total",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("table", "customer")


class OrderItemTenantAdmin(TenantModelAdmin):
    """Admin for order items."""

    permission_resource = "orders"
    restaurant_field = None  # OrderItem doesn't have direct restaurant FK

    list_display = [
        "item_name",
        "order",
        "quantity",
        "unit_price",
        "total_price",
        "status",
    ]
    list_filter = ["status", "preparation_station"]
    search_fields = ["item_name", "order__order_number"]
    ordering = ["-created_at"]

    def get_queryset(self, request):
        """Filter by restaurant via order."""
        qs = super().get_queryset(request)
        restaurant = getattr(request, "restaurant", None)
        if restaurant:
            qs = qs.filter(order__restaurant=restaurant)
        return qs.select_related("order", "menu_item")


class OrderStatusHistoryTenantAdmin(TenantModelAdmin):
    """Admin for order status history."""

    permission_resource = "orders"
    restaurant_field = None

    list_display = ["order", "from_status", "to_status", "changed_by", "created_at"]
    list_filter = ["to_status", "created_at"]
    search_fields = ["order__order_number"]
    readonly_fields = ["order", "from_status", "to_status", "changed_by", "created_at"]
    ordering = ["-created_at"]

    def get_queryset(self, request):
        """Filter by restaurant via order."""
        qs = super().get_queryset(request)
        restaurant = getattr(request, "restaurant", None)
        if restaurant:
            qs = qs.filter(order__restaurant=restaurant)
        return qs.select_related("order", "changed_by")

    def has_add_permission(self, request):
        """Status history is auto-generated, not manually added."""
        return False

    def has_change_permission(self, request, obj=None):
        """Status history is immutable."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Status history should not be deleted."""
        return False


# =============================================================================
# Tables Admin
# =============================================================================


class TableSectionTenantAdmin(TenantModelAdmin):
    """Admin for table sections."""

    permission_resource = "tables"
    list_display = ["name", "display_order", "is_active"]
    list_filter = ["is_active"]
    list_editable = ["display_order", "is_active"]
    search_fields = ["name"]
    ordering = ["display_order"]


class TableTenantAdmin(TenantModelAdmin):
    """Admin for tables."""

    permission_resource = "tables"
    list_display = [
        "number",
        "name",
        "section",
        "capacity",
        "status",
        "is_active",
    ]
    list_filter = ["status", "is_active", "section", "shape"]
    list_editable = ["status", "is_active"]
    search_fields = ["number", "name"]
    ordering = ["section__display_order", "number"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("section")


class TableQRCodeTenantAdmin(TenantModelAdmin):
    """Admin for table QR codes."""

    permission_resource = "tables"
    restaurant_field = None  # QR code links through table

    list_display = ["table", "name", "is_active", "qr_preview", "download_link", "scans_count"]
    list_filter = ["is_active"]
    search_fields = ["code", "name", "table__number"]
    readonly_fields = ["code", "scans_count", "last_scanned_at", "qr_code_display", "qr_url_display"]
    ordering = ["table__number"]
    fieldsets = (
        (None, {
            "fields": ("table", "name", "is_active")
        }),
        ("QR Code", {
            "fields": ("qr_code_display", "qr_url_display", "code"),
            "description": "QR code is auto-generated when you save.",
        }),
        ("Statistics", {
            "fields": ("scans_count", "last_scanned_at"),
        }),
    )

    def get_queryset(self, request):
        """Filter by restaurant via table."""
        qs = super().get_queryset(request)
        restaurant = getattr(request, "restaurant", None)
        if restaurant:
            qs = qs.filter(table__restaurant=restaurant)
        return qs.select_related("table")

    @admin.display(description="QR Code")
    def qr_code_display(self, obj):
        """Display QR code image in detail view."""
        from django.utils.html import format_html
        if obj.qr_image:
            return format_html(
                '<img src="{}" style="max-width: 200px; max-height: 200px; border: 1px solid #ccc; padding: 10px; background: white;" />'
                '<br><a href="{}" download class="button" style="margin-top: 10px; display: inline-block;">Download QR Code</a>',
                obj.qr_image.url,
                obj.qr_image.url
            )
        return "QR code will be generated after save"

    @admin.display(description="QR URL")
    def qr_url_display(self, obj):
        """Display the URL encoded in the QR code."""
        from django.utils.html import format_html
        url = obj.get_qr_url()
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.display(description="Preview")
    def qr_preview(self, obj):
        """Small QR preview for list view."""
        from django.utils.html import format_html
        if obj.qr_image:
            return format_html(
                '<img src="{}" style="width: 50px; height: 50px;" />',
                obj.qr_image.url
            )
        return "-"

    @admin.display(description="Download")
    def download_link(self, obj):
        """Download link for list view."""
        from django.utils.html import format_html
        if obj.qr_image:
            return format_html(
                '<a href="{}" download>Download</a>',
                obj.qr_image.url
            )
        return "-"


class TableSessionTenantAdmin(TenantModelAdmin):
    """Admin for table sessions."""

    permission_resource = "tables"
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


class StaffRoleTenantAdmin(TenantModelAdmin):
    """Admin for staff roles."""

    permission_resource = "staff"
    list_display = ["name", "display_name", "is_system_role"]
    list_filter = ["name", "is_system_role"]
    search_fields = ["name", "display_name"]
    ordering = ["name"]

    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of system roles."""
        if obj and obj.is_system_role:
            return False
        return super().has_delete_permission(request, obj)


class StaffMemberTenantAdmin(TenantModelAdmin):
    """Admin for staff members."""

    permission_resource = "staff"
    list_display = ["user", "role", "is_active", "joined_at"]
    list_filter = ["role", "is_active"]
    list_editable = ["is_active"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]
    ordering = ["-created_at"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user", "role")


class StaffInvitationTenantAdmin(TenantModelAdmin):
    """Admin for staff invitations."""

    permission_resource = "staff"
    list_display = ["email", "role", "status", "invited_by", "expires_at"]
    list_filter = ["status", "role"]
    search_fields = ["email"]
    readonly_fields = ["token", "accepted_at", "accepted_by"]
    ordering = ["-created_at"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("role", "invited_by")


# =============================================================================
# Reservations Admin
# =============================================================================


class ReservationTenantAdmin(TenantModelAdmin):
    """Admin for reservations."""

    permission_resource = "reservations"
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
    search_fields = [
        "confirmation_code",
        "guest_name",
        "guest_email",
        "guest_phone",
    ]
    readonly_fields = ["confirmation_code", "created_at", "updated_at"]
    ordering = ["reservation_date", "reservation_time"]
    date_hierarchy = "reservation_date"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("table", "customer")


class ReservationSettingsTenantAdmin(TenantModelAdmin):
    """Admin for reservation settings."""

    permission_resource = "reservations"
    list_display = [
        "restaurant",
        "accepts_reservations",
        "min_party_size",
        "max_party_size",
    ]

    def has_add_permission(self, request):
        """Only one settings object per restaurant."""
        restaurant = getattr(request, "restaurant", None)
        if restaurant and ReservationSettings.objects.filter(restaurant=restaurant).exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        """Settings should not be deleted."""
        return False


class ReservationBlockedTimeTenantAdmin(TenantModelAdmin):
    """Admin for blocked reservation times."""

    permission_resource = "reservations"
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


class RestaurantHoursInline(UnfoldTabularInline):
    """Inline for restaurant operating hours."""

    model = RestaurantHours
    extra = 0
    max_num = 7
    ordering = ["day_of_week"]


class RestaurantSettingsAdmin(UnfoldModelAdmin):
    """
    Admin for editing the current restaurant's settings.

    Only shows the current restaurant - no list view needed.
    """

    list_display = ["name", "is_active", "default_currency", "timezone"]
    readonly_fields = ["slug", "owner", "average_rating", "total_reviews", "total_orders", "created_at", "updated_at"]
    inlines = [RestaurantHoursInline]
    filter_horizontal = ["amenities"]

    fieldsets = (
        ("Basic Info", {
            "fields": ("name", "slug", "description", "category", "is_active"),
        }),
        ("Amenities", {
            "fields": ("amenities",),
        }),
        ("Contact", {
            "fields": ("email", "phone", "website"),
        }),
        ("Address", {
            "fields": ("address", "city", "postal_code", "country", "latitude", "longitude"),
        }),
        ("Branding", {
            "fields": ("logo", "cover_image", "primary_color", "secondary_color"),
        }),
        ("Settings", {
            "fields": ("default_currency", "timezone", "default_language"),
        }),
        ("Features", {
            "fields": ("accepts_remote_orders", "accepts_reservations", "accepts_takeaway"),
        }),
        ("Orders & Pricing", {
            "fields": ("tax_rate", "service_charge", "minimum_order_amount", "average_preparation_time"),
        }),
        ("Statistics (read-only)", {
            "fields": ("average_rating", "total_reviews", "total_orders"),
            "classes": ("collapse",),
        }),
        ("System", {
            "fields": ("owner", "created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    def get_queryset(self, request):
        """Only show the current restaurant."""
        qs = super().get_queryset(request)
        restaurant = getattr(request, "restaurant", None)
        if restaurant:
            return qs.filter(pk=restaurant.pk)
        return qs.none()

    def has_add_permission(self, request):
        """Don't allow adding new restaurants from tenant admin."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Don't allow deleting the restaurant from tenant admin."""
        return False

    def has_module_permission(self, request):
        """Check if user can see this model in admin sidebar."""
        restaurant = getattr(request, "restaurant", None)
        if not restaurant:
            return False
        if request.user.is_superuser:
            return True
        # Check if user is staff of this restaurant with settings read permission
        try:
            staff = request.user.staff_memberships.get(restaurant=restaurant, is_active=True)
            return "read" in staff.get_effective_permissions().get("settings", [])
        except Exception:
            return False

    def has_view_permission(self, request, obj=None):
        """Allow viewing if user has staff access."""
        restaurant = getattr(request, "restaurant", None)
        if not restaurant:
            return False
        if request.user.is_superuser:
            return True
        # Check if user is staff of this restaurant
        return request.user.staff_memberships.filter(restaurant=restaurant, is_active=True).exists()

    def has_change_permission(self, request, obj=None):
        """Only owner or manager can change settings."""
        restaurant = getattr(request, "restaurant", None)
        if not restaurant:
            return False
        if request.user.is_superuser:
            return True
        if restaurant.owner == request.user:
            return True
        # Check for manager role
        try:
            staff = request.user.staff_memberships.get(restaurant=restaurant, is_active=True)
            return staff.role and staff.role.name == "manager"
        except Exception:
            return False


# =============================================================================
# Register all models with tenant_admin_site
# =============================================================================

# Restaurant Settings
tenant_admin_site.register(Restaurant, RestaurantSettingsAdmin)

# Menu
tenant_admin_site.register(MenuCategory, MenuCategoryTenantAdmin)
tenant_admin_site.register(MenuItem, MenuItemTenantAdmin)
tenant_admin_site.register(ModifierGroup, ModifierGroupTenantAdmin)
tenant_admin_site.register(Modifier, ModifierTenantAdmin)

# Orders (temporarily hidden)
# tenant_admin_site.register(Order, OrderTenantAdmin)
# tenant_admin_site.register(OrderItem, OrderItemTenantAdmin)
# tenant_admin_site.register(OrderStatusHistory, OrderStatusHistoryTenantAdmin)

# Tables
tenant_admin_site.register(TableSection, TableSectionTenantAdmin)
tenant_admin_site.register(Table, TableTenantAdmin)
tenant_admin_site.register(TableQRCode, TableQRCodeTenantAdmin)
tenant_admin_site.register(TableSession, TableSessionTenantAdmin)

# Staff
tenant_admin_site.register(StaffRole, StaffRoleTenantAdmin)
tenant_admin_site.register(StaffMember, StaffMemberTenantAdmin)
tenant_admin_site.register(StaffInvitation, StaffInvitationTenantAdmin)

# Reservations (temporarily hidden)
# tenant_admin_site.register(Reservation, ReservationTenantAdmin)
# tenant_admin_site.register(ReservationSettings, ReservationSettingsTenantAdmin)
# tenant_admin_site.register(ReservationBlockedTime, ReservationBlockedTimeTenantAdmin)
