"""Sold-out sync: the warehouse asks us to flip a dish / modifier on Wolt."""

from __future__ import annotations

from apps.delivery.errors import PlatformClientError
from apps.delivery.wolt.client import build_client
from apps.delivery.wolt.menu import build_item_update
from apps.inventory.platforms.base import PlatformAdapter, PlatformResult


class WoltAdapter(PlatformAdapter):
    code = "wolt"

    def set_item_availability(self, link, menu_item, available, *, task=None):
        kind, data = build_item_update(menu_item, available)
        try:
            client = build_client(link)
            if kind == "options":
                client.update_option_values(data)
            else:
                client.update_items(data)
        except PlatformClientError as exc:
            return PlatformResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - config errors surface on the checklist row
            return PlatformResult(ok=False, error=str(exc))
        return PlatformResult(ok=True, manual=False, external_ref=data[0]["external_id"])
