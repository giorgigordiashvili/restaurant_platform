from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ReferralsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.referrals"
    verbose_name = _("Referrals")
