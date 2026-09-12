from django.apps import AppConfig


class DeliveryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.delivery"
    label = "delivery"
    verbose_name = "Delivery platforms"

    def ready(self):
        # Register the real adapters with the warehouse's sold-out sync.
        from apps.delivery.bolt_food.adapter import BoltFoodAdapter
        from apps.delivery.glovo.adapter import GlovoAdapter
        from apps.delivery.wolt.adapter import WoltAdapter
        from apps.inventory.platforms import API_ADAPTERS

        API_ADAPTERS.update({"glovo": GlovoAdapter, "wolt": WoltAdapter, "bolt_food": BoltFoodAdapter})
