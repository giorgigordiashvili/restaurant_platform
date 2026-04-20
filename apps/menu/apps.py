from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class MenuConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.menu"
    verbose_name = _("Menu")

    def ready(self):
        from apps.core.blurhash_signals import register_blurhash

        from .models import MenuCategory, MenuItem

        register_blurhash(MenuCategory, image_field="image", blurhash_field="image_blurhash")
        register_blurhash(MenuItem, image_field="image", blurhash_field="image_blurhash")
