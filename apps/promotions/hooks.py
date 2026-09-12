"""Never-raise entry points for the order flow."""

from __future__ import annotations

import logging

from apps.promotions import services

logger = logging.getLogger(__name__)


def on_order_items_changed(order, *, channel: str = "") -> None:
    """After items were added (order creation, POS add-item): apply / refresh happy-hour lines."""
    try:
        if services.enabled(order.restaurant):
            services.apply_automatic(order, channel=channel or order.source)
    except Exception:
        logger.exception("Happy-hour application failed for order %s", order.pk)


def on_module_toggled(restaurant, enabled, *, by=None) -> None:
    return None
