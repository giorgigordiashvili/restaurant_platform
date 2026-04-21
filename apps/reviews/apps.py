from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ReviewsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reviews"
    verbose_name = _("Reviews")

    def ready(self):
        # Registers Review post_save / post_delete signals that rebuild
        # Restaurant.average_rating + total_reviews.
        from . import signals  # noqa: F401
