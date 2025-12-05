"""
Custom AdminSite for tenant (restaurant) admin.

Uses django-unfold for modern Tailwind-based UI.
Restaurant staff access their admin at {restaurant-slug}.admin.aimenu.ge/admin/
"""

from django.contrib.admin.apps import AdminConfig
from unfold.sites import UnfoldAdminSite


class TenantAdminSite(UnfoldAdminSite):
    """
    Restaurant-specific admin site with modern unfold UI.

    Accessible at {restaurant-slug}.admin.aimenu.ge/admin/
    Only staff members of the restaurant can access.
    """

    site_header = "Restaurant Dashboard"
    site_title = "Restaurant Dashboard"
    index_title = "Dashboard"

    # Model name to permission resource mapping
    MODEL_TO_RESOURCE = {
        # Restaurant Settings
        "restaurant": "settings",
        # Menu
        "menuitem": "menu",
        "menucategory": "menu",
        "modifiergroup": "menu",
        "modifier": "menu",
        # Orders
        "order": "orders",
        "orderitem": "orders",
        "orderstatushistory": "orders",
        # Tables
        "table": "tables",
        "tablesection": "tables",
        "tableqrcode": "tables",
        "tablesession": "tables",
        "tablesessionguest": "tables",
        # Staff
        "staffmember": "staff",
        "staffrole": "staff",
        "staffinvitation": "staff",
        # Reservations
        "reservation": "reservations",
        "reservationsettings": "reservations",
        "reservationblockedtime": "reservations",
        "reservationhistory": "reservations",
        # Payments
        "payment": "payments",
        "refund": "payments",
    }

    def has_permission(self, request):
        """
        Check if user has permission to access this admin site.

        Returns True if:
        - User is active and authenticated
        - User is a superuser (for debugging)
        - User is a staff member of the current restaurant
        """
        if not request.user.is_active or not request.user.is_authenticated:
            return False

        restaurant = getattr(request, "restaurant", None)
        if not restaurant:
            return False

        # Allow superusers for debugging
        if request.user.is_superuser:
            return True

        # Check if user is staff of this restaurant
        return request.user.staff_memberships.filter(restaurant=restaurant, is_active=True).exists()

    def get_app_list(self, request, app_label=None):
        """
        Filter the app list based on staff role permissions.

        Only shows models that the user's role has 'read' permission for.
        """
        app_list = super().get_app_list(request, app_label)

        # Superusers see everything
        if request.user.is_superuser:
            return app_list

        return self._filter_by_role_permissions(request, app_list)

    def _filter_by_role_permissions(self, request, app_list):
        """Filter models based on StaffRole permissions."""
        restaurant = getattr(request, "restaurant", None)
        if not restaurant:
            return []

        # Get user's staff membership and permissions
        try:
            staff = request.user.staff_memberships.get(restaurant=restaurant, is_active=True)
            permissions = staff.get_effective_permissions()
        except Exception:
            return []

        filtered_apps = []
        for app in app_list:
            filtered_models = []
            for model in app.get("models", []):
                model_name = model.get("object_name", "").lower()
                resource = self.MODEL_TO_RESOURCE.get(model_name)

                # If model has a mapped resource, check permission
                if resource:
                    resource_perms = permissions.get(resource, [])
                    if "read" in resource_perms:
                        filtered_models.append(model)
                # If no mapping, don't show (conservative approach)

            if filtered_models:
                app_copy = app.copy()
                app_copy["models"] = filtered_models
                filtered_apps.append(app_copy)

        return filtered_apps

    def each_context(self, request):
        """Add restaurant context to all admin pages."""
        context = super().each_context(request)
        restaurant = getattr(request, "restaurant", None)

        if restaurant:
            context["restaurant"] = restaurant
            context["site_header"] = f"{restaurant.name} Dashboard"
            context["site_title"] = f"{restaurant.name} Dashboard"

        return context


# Single instance for tenant admin
tenant_admin_site = TenantAdminSite(name="tenant_admin")
