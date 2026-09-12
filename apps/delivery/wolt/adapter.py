"""Wolt: config shape reserved; the API integration is not written yet (degrades to the manual checklist)."""

from apps.inventory.platforms.base import PlatformAdapter


class WoltAdapter(PlatformAdapter):
    code = "wolt"

    def set_item_availability(self, link, menu_item, available, *, task=None):
        raise NotImplementedError("Wolt API sync is not implemented yet.")
