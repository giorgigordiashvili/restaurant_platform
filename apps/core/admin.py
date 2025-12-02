"""
Core admin configuration with multi-tenant support.

Provides base admin classes for:
- TenantAwareModelAdmin: Filters data by restaurant for staff
- SuperadminOnlyMixin: Restricts access to superusers
- TenantSimulatorMixin: Allows superadmins to simulate restaurant context
- Export mixins for CSV/JSON exports
"""

import csv
import json

from django.contrib import admin
from django.http import HttpResponse
from django.utils import timezone

from apps.staff.models import StaffMember
from apps.tenants.models import Restaurant


# Customize the default admin site
admin.site.site_header = "Restaurant Platform Admin"
admin.site.site_title = "Restaurant Platform"
admin.site.index_title = "Platform Administration"


def _custom_admin_index(self, request, extra_context=None):
    """Custom admin index with dashboard widgets for superadmins."""
    extra_context = extra_context or {}

    if request.user.is_superuser:
        from django.db.models import Sum
        from django.utils import timezone as tz

        from apps.orders.models import Order

        today = tz.now().date()

        extra_context["total_restaurants"] = Restaurant.objects.filter(is_active=True).count()
        extra_context["orders_today"] = Order.objects.filter(created_at__date=today).count()
        extra_context["revenue_today"] = (
            Order.objects.filter(created_at__date=today, status="completed").aggregate(total=Sum("total"))["total"] or 0
        )

        # Simulated restaurant context
        extra_context["available_restaurants"] = Restaurant.objects.filter(is_active=True).order_by("name")
        simulated_id = request.session.get("admin_simulated_restaurant")
        if simulated_id:
            try:
                extra_context["simulated_restaurant"] = Restaurant.objects.get(id=simulated_id)
            except Restaurant.DoesNotExist:
                request.session.pop("admin_simulated_restaurant", None)

    # Call the original index method
    return admin.AdminSite.index(self, request, extra_context)


# Monkey-patch the admin site's index method
import types

admin.site.index = types.MethodType(_custom_admin_index, admin.site)


class SuperadminOnlyMixin:
    """
    Restricts admin access to superusers only.
    Use this for sensitive models like AuditLog.
    """

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class TenantSimulatorMixin:
    """
    Allows superadmin to simulate viewing as a specific restaurant.
    Uses session to store selected restaurant ID.

    When a restaurant is being simulated:
    - Querysets are filtered to that restaurant only
    - A banner shows which restaurant is being simulated
    """

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        if request.user.is_superuser:
            extra_context["available_restaurants"] = Restaurant.objects.filter(is_active=True).order_by("name")
            simulated_id = request.session.get("admin_simulated_restaurant")
            if simulated_id:
                try:
                    extra_context["simulated_restaurant"] = Restaurant.objects.get(id=simulated_id)
                except Restaurant.DoesNotExist:
                    # Clear invalid simulation
                    request.session.pop("admin_simulated_restaurant", None)
        return super().changelist_view(request, extra_context)


class ExportMixin:
    """
    Adds CSV and JSON export actions to admin.
    """

    export_fields = None  # Override to specify fields to export

    def get_export_fields(self, request):
        """Get fields to export. Override in subclass for custom fields."""
        if self.export_fields:
            return self.export_fields
        # Default: use list_display if available, otherwise model fields
        if hasattr(self, "list_display") and self.list_display:
            return [f for f in self.list_display if f != "__str__"]
        return [f.name for f in self.model._meta.fields]

    def export_as_csv(self, request, queryset):
        """Export selected records as CSV."""
        meta = self.model._meta
        fields = self.get_export_fields(request)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="{meta.model_name}_export_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        )

        writer = csv.writer(response)
        writer.writerow(fields)

        for obj in queryset:
            row = []
            for field in fields:
                value = getattr(obj, field, "")
                if callable(value):
                    value = value()
                row.append(str(value) if value is not None else "")
            writer.writerow(row)

        return response

    export_as_csv.short_description = "Export selected as CSV"

    def export_as_json(self, request, queryset):
        """Export selected records as JSON."""
        meta = self.model._meta
        fields = self.get_export_fields(request)

        data = []
        for obj in queryset:
            item = {}
            for field in fields:
                value = getattr(obj, field, None)
                if callable(value):
                    value = value()
                # Handle special types
                if hasattr(value, "isoformat"):
                    value = value.isoformat()
                elif hasattr(value, "pk"):
                    value = str(value.pk)
                item[field] = value
            data.append(item)

        response = HttpResponse(content_type="application/json")
        response["Content-Disposition"] = (
            f'attachment; filename="{meta.model_name}_export_{timezone.now().strftime("%Y%m%d_%H%M%S")}.json"'
        )
        response.write(json.dumps(data, indent=2, default=str))

        return response

    export_as_json.short_description = "Export selected as JSON"


class TenantAwareModelAdmin(TenantSimulatorMixin, ExportMixin, admin.ModelAdmin):
    """
    Base admin class for multi-tenant models.

    Features:
    - Superusers see all data with restaurant filter in sidebar
    - Staff users see only their restaurant's data
    - Supports tenant simulation for superadmins
    - Includes CSV/JSON export actions

    Usage:
        class MyModelAdmin(TenantAwareModelAdmin):
            tenant_field = 'restaurant'  # or 'order__restaurant' for nested
    """

    # Field name that links to Restaurant (override in subclasses if different)
    # Use '__' for nested relationships, e.g., 'order__restaurant'
    tenant_field = "restaurant"

    # Default actions include export
    actions = ["export_as_csv", "export_as_json"]

    def get_queryset(self, request):
        """
        Filter queryset based on user permissions and simulation.

        - Superusers: See all, or filtered by simulated restaurant
        - Staff: See only their restaurant's data
        """
        qs = super().get_queryset(request)

        if not self.tenant_field:
            return qs

        if request.user.is_superuser:
            # Check for simulated restaurant
            simulated = request.session.get("admin_simulated_restaurant")
            if simulated:
                return qs.filter(**{self.tenant_field: simulated})
            return qs

        # For staff users, filter by their restaurant memberships
        if request.user.is_authenticated:
            restaurants = StaffMember.objects.filter(user=request.user, is_active=True).values_list(
                "restaurant_id", flat=True
            )
            return qs.filter(**{f"{self.tenant_field}__in": list(restaurants)})

        return qs.none()

    def get_list_filter(self, request):
        """Add restaurant filter for superadmins at the top of filters."""
        filters = list(super().get_list_filter(request) or [])

        if request.user.is_superuser and self.tenant_field:
            # Add restaurant filter if not already present
            if self.tenant_field not in filters:
                filters.insert(0, self.tenant_field)

        return filters

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """
        Filter foreign key choices based on tenant context.
        Only applies when simulating a restaurant.
        """
        if request.user.is_superuser:
            simulated = request.session.get("admin_simulated_restaurant")
            if simulated:
                # Filter related objects by simulated restaurant
                if hasattr(db_field.related_model, "restaurant"):
                    kwargs["queryset"] = db_field.related_model.objects.filter(restaurant_id=simulated)
                elif hasattr(db_field.related_model, "restaurant_id"):
                    kwargs["queryset"] = db_field.related_model.objects.filter(restaurant_id=simulated)

        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ReadOnlyAdminMixin:
    """
    Makes an admin interface read-only.
    Useful for audit logs and history tables.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TenantAwareReadOnlyAdmin(SuperadminOnlyMixin, ReadOnlyAdminMixin, TenantAwareModelAdmin):
    """
    Read-only admin for superusers only.
    Perfect for audit logs and sensitive data.
    """

    pass


# Bulk action helpers
def make_active(modeladmin, request, queryset):
    """Bulk action to activate selected items."""
    updated = queryset.update(is_active=True)
    modeladmin.message_user(request, f"{updated} item(s) activated.")


make_active.short_description = "Activate selected items"


def make_inactive(modeladmin, request, queryset):
    """Bulk action to deactivate selected items."""
    updated = queryset.update(is_active=False)
    modeladmin.message_user(request, f"{updated} item(s) deactivated.")


make_inactive.short_description = "Deactivate selected items"


# For models using django-parler (TranslatableModel)
# Import in a try block to avoid dependency issues
try:
    from parler.admin import TranslatableAdmin

    class TenantAwareTranslatableAdmin(TenantSimulatorMixin, ExportMixin, TranslatableAdmin):
        """
        Base admin class for multi-tenant translatable models.
        Combines TenantAwareModelAdmin features with parler's TranslatableAdmin.
        """

        tenant_field = "restaurant"
        actions = ["export_as_csv", "export_as_json"]

        def get_queryset(self, request):
            """Filter queryset based on user permissions and simulation."""
            qs = super().get_queryset(request)

            if not self.tenant_field:
                return qs

            if request.user.is_superuser:
                simulated = request.session.get("admin_simulated_restaurant")
                if simulated:
                    return qs.filter(**{self.tenant_field: simulated})
                return qs

            if request.user.is_authenticated:
                restaurants = StaffMember.objects.filter(user=request.user, is_active=True).values_list(
                    "restaurant_id", flat=True
                )
                return qs.filter(**{f"{self.tenant_field}__in": list(restaurants)})

            return qs.none()

        def get_list_filter(self, request):
            """Add restaurant filter for superadmins at the top of filters."""
            filters = list(super().get_list_filter(request) or [])

            if request.user.is_superuser and self.tenant_field:
                if self.tenant_field not in filters:
                    filters.insert(0, self.tenant_field)

            return filters

        def formfield_for_foreignkey(self, db_field, request, **kwargs):
            """Filter foreign key choices based on tenant context."""
            if request.user.is_superuser:
                simulated = request.session.get("admin_simulated_restaurant")
                if simulated:
                    if hasattr(db_field.related_model, "restaurant"):
                        kwargs["queryset"] = db_field.related_model.objects.filter(restaurant_id=simulated)
                    elif hasattr(db_field.related_model, "restaurant_id"):
                        kwargs["queryset"] = db_field.related_model.objects.filter(restaurant_id=simulated)

            return super().formfield_for_foreignkey(db_field, request, **kwargs)

except ImportError:
    # parler not installed, skip TenantAwareTranslatableAdmin
    TenantAwareTranslatableAdmin = None
