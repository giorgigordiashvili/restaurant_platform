from .base import PlatformAdapter


class WoltAdapter(PlatformAdapter):
    """Placeholder for the Wolt Merchant API integration."""

    code = "wolt"

    def set_item_availability(self, link, menu_item, available, *, task=None):
        raise NotImplementedError("Wolt API sync is not implemented yet.")
