from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Core"

    def ready(self):
        """Configure admin sites and register models."""
        from django.contrib import admin

        # Import tenant_admin to register models with tenant_admin_site
        from apps.core import tenant_admin  # noqa: F401

        # Customize the default admin site (after unfold may have replaced it)
        admin.site.site_header = "Restaurant Platform Admin"
        admin.site.site_title = "Restaurant Platform"
        admin.site.index_title = "Platform Administration"
