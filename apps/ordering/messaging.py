"""Guest SMS / email for first-party online orders (templates live in the Notifications settings)."""

from __future__ import annotations

import logging

from django.conf import settings

from apps.notifications import services as notifications

logger = logging.getLogger(__name__)


def _language(order) -> str:
    user = getattr(order, "customer", None)
    return (getattr(user, "preferred_language", None) if user else None) or order.restaurant.default_language or "ka"


def order_url(order) -> str:
    base = getattr(settings, "FRONTEND_BASE_URL", "https://aimenu.ge").rstrip("/")
    return f"{base}/orders/{order.order_number}"


def _send(order, kind: str, **ctx) -> None:
    try:
        r = order.restaurant
        if not notifications.enabled(r):
            return
        cfg = notifications.settings_for(r)
        text = notifications.render(
            cfg.template(kind, _language(order)),
            name=order.customer_name or "",
            restaurant=r.name,
            order=order.order_number,
            link=order_url(order),
            **ctx,
        )
        if order.customer_phone and cfg.guest_sms:
            notifications.send_message(r, "sms", order.customer_phone, text, kind=kind, ref=order)
        if order.customer_email and cfg.guest_email:
            notifications.send_message(r, "email", order.customer_email, text, subject=r.name, kind=kind, ref=order)
    except Exception:  # noqa: BLE001 - messaging never breaks the order flow
        logger.exception("guest message %s failed for %s", kind, order.pk)


def order_accepted(order) -> None:
    eta = order.estimated_ready_at
    _send(order, "order_accepted", time=eta.astimezone().strftime("%H:%M") if eta else "")


def order_ready(order) -> None:
    _send(order, "order_ready")


def order_on_the_way(order, delivery) -> None:
    eta = delivery.dropoff_eta
    _send(
        order,
        "order_on_the_way",
        tracking_url=delivery.tracking_url or order_url(order),
        time=eta.astimezone().strftime("%H:%M") if eta else "",
        courier=delivery.courier_name or "",
    )
