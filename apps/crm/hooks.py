"""Never-raise entry points called from orders, reservations, reviews and the modules registry."""

from __future__ import annotations

import logging

from apps.crm import services

logger = logging.getLogger(__name__)


def _safe(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception("crm hook %s failed", fn.__name__)
            return None

    wrapper.__name__ = fn.__name__
    return wrapper


@_safe
def on_order_created(order, *, consent=False):
    """Create the guest record at order time (consent from the checkout checkbox); stats update on completion."""
    if not services.enabled(order.restaurant):
        return None
    c = services.identify(
        order.restaurant,
        user=order.customer,
        phone=order.customer_phone,
        email=order.customer_email,
        name=order.customer_name,
        source=order.source,
    )
    if c is not None and consent:
        services.set_consent(c, True, source="checkout")
    return c


@_safe
def on_order_completed(order, *, by=None):
    return services.touch_from_order(order)


@_safe
def on_reservation_done(reservation, *, by=None):
    return services.touch_from_reservation(reservation)


@_safe
def on_reservation_created(reservation, *, consent=False):
    if not consent:
        return None
    return services.touch_from_reservation(reservation, consent=True)


@_safe
def on_review_created(review):
    return services.touch_from_review(review)


@_safe
def on_profile_consent_changed(user):
    return services.sync_user_consent(user)


@_safe
def on_module_toggled(restaurant, enabled, *, by=None):
    if not enabled:
        return None
    services.seed_segments(restaurant)
    from apps.core.enqueue import enqueue
    from apps.crm import tasks

    enqueue(tasks.backfill, str(restaurant.pk))
    return None
