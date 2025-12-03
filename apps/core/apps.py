from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Core"

    def ready(self):
        """Configure admin sites and register models."""
        from django.contrib import admin
        from django.contrib.auth.admin import UserAdmin

        # Import tenant_admin to register models with tenant_admin_site
        from apps.core import tenant_admin  # noqa: F401

        # Manually register models with admin.site since unfold may have
        # replaced it after Django's autodiscovery ran
        from apps.accounts.models import User, UserProfile
        from apps.tenants.models import Restaurant, RestaurantHours
        from apps.audit.models import AuditLog

        # Import admin classes
        from apps.accounts.admin import UserAdmin as CustomUserAdmin, UserProfileAdmin
        from apps.tenants.admin import RestaurantAdmin, RestaurantHoursAdmin
        from apps.audit.admin import AuditLogAdmin

        # Register if not already registered
        if User not in admin.site._registry:
            admin.site.register(User, CustomUserAdmin)
        if UserProfile not in admin.site._registry:
            admin.site.register(UserProfile, UserProfileAdmin)
        if Restaurant not in admin.site._registry:
            admin.site.register(Restaurant, RestaurantAdmin)
        if RestaurantHours not in admin.site._registry:
            admin.site.register(RestaurantHours, RestaurantHoursAdmin)
        if AuditLog not in admin.site._registry:
            admin.site.register(AuditLog, AuditLogAdmin)

        # Customize the default admin site
        admin.site.site_header = "Restaurant Platform Admin"
        admin.site.site_title = "Restaurant Platform"
        admin.site.index_title = "Platform Administration"
