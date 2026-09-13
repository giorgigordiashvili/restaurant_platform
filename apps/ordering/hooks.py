"""Never-raise entry points called from the order state machine and the modules registry."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def on_order_status_changed(order, old_status, new_status, *, by=None) -> None:
    try:
        from apps.ordering import dispatch

        dispatch.on_order_status_changed(order, old_status, new_status, by=by)
    except Exception:
        logger.exception("ordering status hook failed for order %s", order.pk)


def on_module_toggled(restaurant, enabled, *, by=None) -> None:
    if not enabled:
        return
    try:
        from apps.ordering import services

        services.settings_for(restaurant)
        from apps.delivery.models import RestaurantDeliveryPlatform

        for code in RestaurantDeliveryPlatform.COURIERS:
            RestaurantDeliveryPlatform.objects.get_or_create(
                restaurant=restaurant, platform=code, defaults={"is_enabled": False}
            )
    except Exception:
        logger.exception("ordering module hook failed for %s", restaurant.slug)
