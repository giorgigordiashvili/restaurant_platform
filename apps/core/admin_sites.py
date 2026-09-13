"""
Custom AdminSite for tenant (restaurant) admin.

Uses django-unfold for modern Tailwind-based UI.
Restaurant staff access their admin at {restaurant-slug}.admin.aimenu.ge/admin/
"""

from unfold.sites import UnfoldAdminSite

from apps.menu.admin_autocomplete import PickerAutocompleteJsonView


class TenantAdminSite(UnfoldAdminSite):
    """
    Restaurant-specific admin site with modern unfold UI.

    Accessible at {restaurant-slug}.admin.aimenu.ge/admin/
    Only staff members of the restaurant can access.
    """

    site_header = "Restaurant Dashboard"
    site_title = "Restaurant Dashboard"
    index_title = "Dashboard"
    # Own Unfold config (module-aware sidebar) -- the platform /admin/ keeps UNFOLD.
    settings_name = "UNFOLD_TENANT"
    index_template = "admin/tenant_index.html"

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
        # Reports (proxy models in apps.reports)
        "salesreport": "analytics",
        "menureport": "analytics",
        "foodcostreport": "analytics",
        "staffreport": "analytics",
        "shiftsreport": "analytics",
        "reservationsreport": "analytics",
        "reviewsreport": "analytics",
        # Waitlist
        "waitlistentry": "reservations",
        "waitlistsettingspage": "settings",
        # Terminals
        "paymentterminal": "cash",
        "terminaltransaction": "cash",
        "terminalreconciliation": "cash",
        # Online ordering
        "onlineorderingsettingspage": "settings",
        "deliveryzone": "settings",
        "courier": "staff",
        "delivery": "orders",
        "restaurantdomain": "settings",
        # Delivery
        "deliveryplatformspage": "settings",
        "deliveryplatformevent": "orders",
        "platformmenusync": "settings",
        # Fiscal
        "fiscalsettingspage": "fiscal",
        "fiscaldocument": "fiscal",
        "fiscalprofile": "fiscal",
        # CRM
        "customer": "crm",
        "segment": "crm",
        "campaign": "crm",
        "automation": "crm",
        "crmreport": "analytics",
        # Timekeeping / activity
        "timeentry": "timekeeping",
        "rotashift": "timekeeping",
        "hoursreport": "analytics",
        "activityfeed": "staff",
        # Purchasing
        "purchaseorder": "warehouse",
        "supplier": "warehouse",
        # Promotions
        "promotion": "menu",
        "menuschedule": "menu",
        # Notifications
        "notificationsettingspage": "settings",
        "outboundmessage": "settings",
        # Printing
        "printer": "settings",
        "printjob": "orders",
        # Cash & payments
        "payment": "cash",
        "refund": "cash",
        "cashshift": "cash",
        "cashmovement": "cash",
        "discountreason": "cash",
        "paymentallocation": "cash",
        # Shared venue (food hall): the page lives under settings; registry rows under tables.
        "venuesharerequest": "settings",
        "venue": "settings",
        "venuetable": "tables",
        "venuesection": "tables",
        # Loyalty — piggybacks on the menu-manager permission bucket.
        "loyaltyprogram": "menu",
        "loyaltycounter": "menu",
        "loyaltyredemption": "menu",
        # Reviews share the menu-manager bucket too.
        "review": "menu",
        "reviewreport": "menu",
        # Modules page + hours inline live with settings.
        "restaurantmodules": "settings",
        "restauranthours": "settings",
        # Warehouse (hidden entirely until Restaurant.warehouse_enabled).
        "warehouseoverview": "warehouse",
        "stockitem": "warehouse",
        "stocklot": "warehouse",
        "stockmovement": "warehouse",
        "stockadjustment": "warehouse",
        "inventoryalert": "warehouse",
        "recipeline": "warehouse",
        "wasteentry": "warehouse_logs",
        "employeemeal": "warehouse_logs",
        "restaurantdeliveryplatform": "settings",
    }

    def autocomplete_view(self, request):
        # Richer picker text (e.g. modifier groups show their internal name).
        return PickerAutocompleteJsonView.as_view(admin_site=self)(request)

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
        The app list feeds the index and the command palette: drop whatever
        belongs to a module the restaurant has switched off, then keep only
        the models the user's role may read.
        """
        from apps.core import modules

        app_list = super().get_app_list(request, app_label)
        restaurant = getattr(request, "restaurant", None)
        if not restaurant:
            return []

        hidden_apps = modules.hidden_apps(restaurant)
        hidden_models = modules.hidden_models(restaurant)
        pruned = []
        for app in app_list:
            if app.get("app_label") in hidden_apps:
                continue
            kept = [
                m
                for m in app.get("models", [])
                if (app.get("app_label"), m.get("object_name", "").lower()) not in hidden_models
            ]
            if kept:
                pruned.append({**app, "models": kept})

        if request.user.is_superuser:
            return pruned
        return self._filter_by_role_permissions(request, pruned)

    def index(self, request, extra_context=None):
        from apps.core.dashboard import module_cards

        extra_context = dict(extra_context or {})
        if getattr(request, "restaurant", None):
            extra_context["module_cards"] = module_cards(request)
        return super().index(request, extra_context)

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
