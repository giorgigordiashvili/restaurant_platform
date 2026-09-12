"""Sold-out sync: the warehouse asks us to flip a dish / modifier on Glovo."""

from __future__ import annotations

from apps.delivery.glovo.client import GlovoClientError, build_client
from apps.delivery.glovo.menu import build_availability_update
from apps.inventory.platforms.base import PlatformAdapter, PlatformResult


class GlovoAdapter(PlatformAdapter):
    code = "glovo"

    def set_item_availability(self, link, menu_item, available, *, task=None):
        try:
            response = build_client(link).bulk_update(**build_availability_update(menu_item, available))
        except GlovoClientError as exc:
            return PlatformResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - config errors surface on the checklist row
            return PlatformResult(ok=False, error=str(exc))
        return PlatformResult(ok=True, manual=False, external_ref=str(response.get("transactionId", "")))
