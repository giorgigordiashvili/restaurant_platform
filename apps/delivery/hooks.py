"""Never-raise wrappers called from the order state machine."""

from __future__ import annotations

import logging

from apps.delivery import services

logger = logging.getLogger(__name__)


def on_order_status_changed(order, old_status, new_status, *, by=None) -> None:
    try:
        services.on_order_status_changed(order, old_status, new_status)
    except Exception:
        logger.exception("Delivery status hook failed for order %s", order.pk)


def on_order_item_voided(item, *, by=None) -> None:
    try:
        services.on_order_item_voided(item)
    except Exception:
        logger.exception("Delivery refund hook failed for item %s", item.pk)


def on_module_toggled(restaurant, enabled, *, by=None) -> None:
    """Make sure a row exists per platform so the admin page has something to show."""
    if not enabled:
        return
    try:
        from apps.delivery.models import RestaurantDeliveryPlatform

        for code, _label in RestaurantDeliveryPlatform.PLATFORM_CHOICES:
            RestaurantDeliveryPlatform.objects.get_or_create(
                restaurant=restaurant, platform=code, defaults={"is_enabled": False}
            )
    except Exception:
        logger.exception("Could not seed platform links for %s", restaurant.slug)
