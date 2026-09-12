from .base import PlatformAdapter


class GlovoAdapter(PlatformAdapter):
    """Placeholder for the Glovo Partners API integration."""

    code = "glovo"

    def set_item_availability(self, link, menu_item, available, *, task=None):
        raise NotImplementedError("Glovo API sync is not implemented yet.")
