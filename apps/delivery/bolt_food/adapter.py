"""Bolt Food: config shape reserved; the API integration is not written yet (degrades to the manual checklist)."""

from apps.inventory.platforms.base import PlatformAdapter


class BoltFoodAdapter(PlatformAdapter):
    code = "bolt_food"

    def set_item_availability(self, link, menu_item, available, *, task=None):
        raise NotImplementedError("Bolt Food API sync is not implemented yet.")
