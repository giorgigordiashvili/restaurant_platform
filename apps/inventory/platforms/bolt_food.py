from .base import PlatformAdapter


class BoltFoodAdapter(PlatformAdapter):
    """Placeholder for the Bolt Food partner API integration."""

    code = "bolt_food"

    def set_item_availability(self, link, menu_item, available, *, task=None):
        raise NotImplementedError("Bolt Food API sync is not implemented yet.")
