"""
Courier orchestration for first-party delivery orders: request a ride
(own courier or Wolt Drive / Glovo On-Demand), follow its status, tell the
guest, and close the order when it is delivered.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.enqueue import enqueue
from apps.delivery.config import NotConfigured
from apps.delivery.courier import registry
from apps.delivery.courier.base import CourierError, StatusResult
from apps.ordering import services
from apps.ordering.models import Courier, Delivery

logger = logging.getLogger(__name__)


class DispatchError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def delivery_for(order, *, create: bool = True) -> Delivery | None:
    try:
        return order.delivery
    except Delivery.DoesNotExist:
        if not create:
            return None
        d = Delivery.objects.create(
            restaurant=order.restaurant,
            order=order,
            provider=services.settings_for(order.restaurant).courier_provider,
            fee_charged=Decimal(order.delivery_fee or 0),
        )
        order.delivery = d
        return d


def _audit(order, action: str, by, description: str, changes=None) -> None:
    try:
        from apps.audit.services import log_action

        log_action(
            action,
            user=by if getattr(by, "is_authenticated", False) else None,
            restaurant=order.restaurant,
            description=description,
            target_model="Order",
            target_id=str(order.pk),
            changes=changes or {},
        )
    except Exception:  # pragma: no cover
        logger.exception("audit failed")


# ── requesting ─────────────────────────────────────────────────────────────


def request_courier(order, *, provider: str | None = None, by=None, session=None, sync: bool = False) -> Delivery:
    """
    Book a courier for a delivery order. Own couriers just open the delivery
    for assignment; platform providers are called from a task (``sync=True``
    calls them inline -- webhooks/tests).
    """
    if order.order_type != "delivery":
        raise DispatchError("not_delivery", "Only delivery orders need a courier.")
    if order.status in ("cancelled", "pending_payment"):
        raise DispatchError("bad_status", "This order cannot be dispatched.")
    if order.delivery_lat is None or order.delivery_lng is None:
        raise DispatchError("no_coordinates", "This order has no delivery coordinates.")
    with transaction.atomic():
        d = delivery_for(order)
        d = Delivery.objects.select_for_update().get(pk=d.pk)
        if d.is_open:
            return d
        d.provider = provider or d.provider or services.settings_for(order.restaurant).courier_provider
        if not registry.is_configured(order.restaurant, d.provider):
            raise DispatchError("not_configured", f"{d.get_provider_display()} is not connected.")
        d.status = "requested"
        d.error = ""
        d.requested_at = timezone.now()
        d.requested_by = by if getattr(by, "is_authenticated", False) else None
        d.log("requested", provider=d.provider, by=getattr(by, "email", None))
        d.save()
        order.delivery = d  # replace the stale cached related object
    _audit(order, "courier_requested", by, f"Courier requested via {d.get_provider_display()}")
    if d.provider == "own":
        return d
    if sync:
        run_platform_request(d, session=session)
    else:
        from apps.ordering import tasks

        enqueue(tasks.request_courier, str(d.pk))
    return d


def run_platform_request(delivery: Delivery, *, session=None) -> Delivery:
    """Quote + create at the platform; called by the task (retries on retryable errors)."""
    order = delivery.order
    try:
        provider = registry.get_provider(order.restaurant, delivery.provider, session=session)
        delivery._lead_minutes = services.lead_for(services.settings_for(order.restaurant), "delivery")
        q = provider.quote(delivery)
        delivery.quote = {**q.as_dict()}
        created = provider.create(delivery)
    except NotConfigured as exc:
        _fail(delivery, str(exc))
        return delivery
    except CourierError as exc:
        if exc.retryable:
            delivery.error = str(exc)
            delivery.log("retry", error=str(exc))
            delivery.save(update_fields=["error", "events", "updated_at"])
            raise
        _fail(delivery, str(exc), payload=exc.payload)
        return delivery
    delivery.external_id = created.external_id
    delivery.status = created.status or "requested"
    delivery.tracking_url = created.tracking_url or delivery.tracking_url
    delivery.pickup_eta = created.pickup_eta
    delivery.dropoff_eta = created.dropoff_eta
    if created.cost is not None:
        delivery.cost = created.cost
    elif q.price:
        delivery.cost = q.price
    if created.raw.get("id"):
        delivery.quote["delivery_id"] = str(created.raw["id"])
    delivery.error = ""
    delivery.log("created", external_id=created.external_id, status=delivery.status)
    delivery.save()
    return delivery


def _fail(delivery: Delivery, error: str, payload=None) -> None:
    delivery.status = "failed"
    delivery.error = error[:2000]
    delivery.log("failed", error=error[:500], payload=payload if isinstance(payload, dict) else None)
    delivery.save(update_fields=["status", "error", "events", "updated_at"])
    from apps.notifications import hooks as notification_hooks

    notification_hooks.on_delivery_failed(delivery)


# ── own couriers ───────────────────────────────────────────────────────────


def assign_own(delivery: Delivery, courier: Courier, *, by=None) -> Delivery:
    if courier.restaurant_id != delivery.restaurant_id:
        raise DispatchError("bad_courier", "Courier belongs to another restaurant.")
    if delivery.status in Delivery.FINAL:
        raise DispatchError("final", "This delivery is already finished.")
    delivery.provider = "own"
    delivery.courier = courier
    delivery.courier_name = courier.name
    delivery.courier_phone = courier.phone
    delivery.status = "assigned"
    if not delivery.requested_at:
        delivery.requested_at = timezone.now()
    delivery.log("assigned", courier=courier.name, by=getattr(by, "email", None))
    delivery.save()
    _audit(delivery.order, "courier_assigned", by, f"Courier {courier.name} assigned")
    return delivery


OWN_TRANSITIONS = {
    "requested": ("assigned", "cancelled"),
    "assigned": ("picked_up", "cancelled", "failed"),
    "picked_up": ("delivered", "failed"),
}


def courier_update(delivery: Delivery, status: str, *, by=None, lat=None, lng=None, note: str = "") -> Delivery:
    """A rider (or a manager) moves an own-courier delivery along."""
    allowed = OWN_TRANSITIONS.get(delivery.status, ())
    if status not in allowed:
        raise DispatchError("bad_transition", f"Cannot go from {delivery.status} to {status}.")
    result = StatusResult(status=status, lat=lat, lng=lng)
    apply_status(delivery, result, source="pos", by=by, note=note)
    return delivery


# ── status (webhooks, polling, POS) ────────────────────────────────────────


def apply_status(delivery: Delivery, result: StatusResult, *, source: str, by=None, note: str = "") -> bool:
    """Write a platform / courier status onto the delivery and trigger side effects. Returns True when it changed."""
    old = delivery.status
    changed = bool(result.status) and result.status != old and old not in Delivery.FINAL
    if result.status and changed:
        delivery.status = result.status
    if result.courier_name:
        delivery.courier_name = result.courier_name
    if result.courier_phone:
        delivery.courier_phone = result.courier_phone
    if result.tracking_url:
        delivery.tracking_url = result.tracking_url
    if result.pickup_eta:
        delivery.pickup_eta = result.pickup_eta
    if result.dropoff_eta:
        delivery.dropoff_eta = result.dropoff_eta
    if result.lat is not None and result.lng is not None:
        delivery.courier_lat = Decimal(str(result.lat)).quantize(Decimal("0.000001"))
        delivery.courier_lng = Decimal(str(result.lng)).quantize(Decimal("0.000001"))
    now = timezone.now()
    if changed and delivery.status == "picked_up":
        delivery.picked_up_at = now
    if changed and delivery.status == "delivered":
        delivery.delivered_at = now
    if changed:
        delivery.log(delivery.status, source=source, note=note or None, by=getattr(by, "email", None))
    delivery.save()
    if changed:
        _after_change(delivery, old, by=by)
    return changed


def _after_change(delivery: Delivery, old: str, *, by=None) -> None:
    from apps.ordering import messaging

    order = delivery.order
    if delivery.status == "picked_up":
        messaging.order_on_the_way(order, delivery)
    elif delivery.status == "delivered":
        from apps.orders.services import transition_order

        if order.status not in ("completed", "cancelled"):
            transition_order(order, "completed", by=by, notes="Delivered by courier")
    elif delivery.status == "failed":
        from apps.notifications import hooks as notification_hooks

        notification_hooks.on_delivery_failed(delivery)


def cancel_courier(delivery: Delivery, *, reason: str = "", by=None, session=None) -> Delivery:
    if delivery.status in Delivery.FINAL:
        return delivery
    if delivery.provider != "own" and delivery.external_id:
        try:
            registry.get_provider(delivery.restaurant, delivery.provider, session=session).cancel(delivery, reason)
        except (CourierError, NotConfigured) as exc:
            delivery.error = f"Cancel at platform failed: {exc}"[:2000]
            delivery.log("cancel_failed", error=str(exc)[:500])
            logger.warning("courier cancel failed for %s: %s", delivery.pk, exc)
    delivery.status = "cancelled"
    delivery.log("cancelled", reason=reason, by=getattr(by, "email", None))
    delivery.save()
    _audit(delivery.order, "courier_cancelled", by, f"Courier cancelled: {reason}"[:200])
    return delivery


def refresh(delivery: Delivery, *, session=None) -> bool:
    if delivery.provider == "own" or delivery.status in Delivery.FINAL:
        return False
    try:
        result = registry.get_provider(delivery.restaurant, delivery.provider, session=session).refresh(delivery)
    except (CourierError, NotConfigured) as exc:
        logger.info("refresh failed for %s: %s", delivery.pk, exc)
        return False
    if result is None:
        return False
    return apply_status(delivery, result, source="poll")


# ── order state machine hook ───────────────────────────────────────────────


def on_order_status_changed(order, old_status: str, new_status: str, *, by=None) -> None:
    """Called from ``transition_order`` for every order; only first-party takeaway / delivery orders react."""
    if order.source not in ("web", "qr") or order.order_type not in ("takeaway", "delivery"):
        return
    from apps.ordering import messaging

    if new_status == "confirmed":
        messaging.order_accepted(order)
    elif new_status == "ready" and order.order_type == "takeaway":
        messaging.order_ready(order)
    if order.order_type != "delivery":
        return
    if not services.enabled(order.restaurant):
        return
    if new_status == "cancelled":
        d = delivery_for(order, create=False)
        if d is not None and d.is_open:
            cancel_courier(d, reason="Order cancelled", by=by)
        return
    cfg = services.settings_for(order.restaurant)
    if cfg.auto_request_courier_on != "manual" and new_status == cfg.auto_request_courier_on:
        d = delivery_for(order, create=False)
        if d is not None and (d.is_open or d.status == "delivered"):
            return
        try:
            request_courier(order, by=by)
        except DispatchError as exc:
            logger.info("auto courier request skipped for %s: %s", order.order_number, exc.message)
