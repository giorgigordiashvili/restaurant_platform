from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def on_module_toggled(restaurant, enabled, *, by=None) -> None:
    if not enabled:
        return
    try:
        from apps.purchasing import services

        services.backfill_suppliers(restaurant)
    except Exception:
        logger.exception("Supplier backfill failed for %s", restaurant.slug)
