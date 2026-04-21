from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TenantsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tenants"
    verbose_name = _("Tenants")

    def ready(self):
        from apps.core.blurhash_signals import register_blurhash, register_many

        from .models import Restaurant, RestaurantCategory

        register_blurhash(RestaurantCategory, image_field="image", blurhash_field="image_blurhash")
        register_many(
            Restaurant,
            [
                ("logo", "logo_blurhash"),
                ("cover_image", "cover_image_blurhash"),
            ],
        )
