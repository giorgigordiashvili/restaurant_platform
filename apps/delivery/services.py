"""
Platform orders in, status updates out.

Inbound (webhook -> ``create_platform_order``): an ``Order`` with
``source="glovo"``, lines at the platform's prices, non-strict stock
reservation, then (when ``auto_accept``) the same confirm the POS "Accept"
button does. Outbound (``on_order_status_changed``): confirmed -> ACCEPTED,
ready -> READY_FOR_PICKUP, pushed by a Celery task with retries.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.enqueue import enqueue
from apps.delivery.models import PLATFORM_SOURCES, DeliveryPlatformEvent, RestaurantDeliveryPlatform
from apps.orders.models import Order, OrderItem, OrderItemModifier, OrderStatusHistory

logger = logging.getLogger(__name__)

STATUS_MAP = {"confirmed": "ACCEPTED", "ready": "READY_FOR_PICKUP"}
TOTAL_TOLERANCE = Decimal("0.05")


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "delivery_enabled", False))


def link_for(restaurant, platform: str) -> RestaurantDeliveryPlatform | None:
    return RestaurantDeliveryPlatform.objects.filter(restaurant=restaurant, platform=platform).first()


def link_for_store(platform: str, store_id: str) -> RestaurantDeliveryPlatform | None:
    if not store_id:
        return None
    return (
        RestaurantDeliveryPlatform.objects.select_related("restaurant")
        .filter(platform=platform, store_external_id=str(store_id))
        .first()
    )


# ── inbound ───────────────────────────────────────────────────────────────


def create_platform_order(link, parsed, *, event=None) -> Order:
    """Runs inside the caller's transaction (the webhook holds the event row lock)."""
    from apps.delivery.glovo.menu import parse_attribute_id, parse_product_id
    from apps.inventory import hooks as inventory_hooks
    from apps.inventory import services as inventory
    from apps.menu.models import MenuItem, Modifier

    restaurant = link.restaurant
    notes = [parsed.special_requirements]
    if parsed.allergy_info:
        notes.append(f"Allergy: {parsed.allergy_info}")
    if parsed.pick_up_code:
        notes.append(f"Pick-up code {parsed.pick_up_code}")
    if parsed.cutlery_requested:
        notes.append("Cutlery requested")
    prep = getattr(link, "prep_time_minutes", 20) or 20
    order = Order.objects.create(
        restaurant=restaurant,
        order_type="takeaway" if parsed.is_picked_up_by_customer else "delivery",
        status="pending",
        source=link.platform,
        external_id=parsed.order_id,
        customer_name=parsed.customer_name,
        customer_phone=parsed.customer_phone[:20],
        customer_notes="\n".join(n for n in notes if n),
        delivery_address=parsed.delivery_address,
        estimated_ready_at=parsed.estimated_pickup_time or (timezone.now() + timezone.timedelta(minutes=prep)),
        platform_data={
            "order_code": parsed.order_code,
            "pickup_eta": parsed.estimated_pickup_time.isoformat() if parsed.estimated_pickup_time else None,
            "courier": {"name": parsed.courier_name, "phone": parsed.courier_phone},
            "payment_method": parsed.payment_method,
            "currency": parsed.currency,
            "raw_total_minor": str(parsed.total_customer_to_pay),
            "is_pickup_by_customer": parsed.is_picked_up_by_customer,
            "raw": parsed.raw,
        },
    )
    for p in parsed.products:
        pk = parse_product_id(p.id)
        menu_item = MenuItem.objects.filter(pk=pk, restaurant=restaurant).first() if pk else None
        item = OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            item_name=p.name or (menu_item.safe_translation_getter("name", any_language=True) if menu_item else "Item"),
            unit_price=p.price,
            quantity=max(p.quantity, 1),
            total_price=p.price * max(p.quantity, 1),
            preparation_station=menu_item.preparation_station if menu_item else "kitchen",
        )
        for a in p.attributes:
            mpk = parse_attribute_id(a.id)
            modifier = Modifier.objects.filter(pk=mpk, group__restaurant=restaurant).first() if mpk else None
            OrderItemModifier.objects.create(
                order_item=item, modifier=modifier, modifier_name=a.name or "Option", price_adjustment=a.price
            )
        item.recalculate_total()  # payload prices, never MenuItem.price
    order.calculate_totals()
    expected = parsed.estimated_total_price or parsed.total_customer_to_pay
    if expected and abs(order.total - expected) > TOTAL_TOLERANCE:
        order.platform_data["total_mismatch"] = {"ours": str(order.total), "platform": str(expected)}
        order.save(update_fields=["platform_data", "updated_at"])
        logger.warning("Glovo order %s total mismatch: ours %s platform %s", parsed.order_id, order.total, expected)
    currency = getattr(restaurant, "default_currency", "GEL")
    if parsed.currency and currency and parsed.currency != currency:
        logger.warning(
            "Glovo order %s currency %s differs from restaurant %s", parsed.order_id, parsed.currency, currency
        )
    # A sold-out dish (availability push lagging) must not lose the order: reserve non-strictly.
    try:
        if inventory.enabled(restaurant):
            inventory.reserve_for_order(order, strict=False)
    except Exception:
        logger.exception("Stock reservation failed for platform order %s", order.pk)
    OrderStatusHistory.objects.create(
        order=order, from_status="", to_status="pending", notes=f"Order from {link.get_platform_display()}"
    )
    if event is not None:
        event.order = order
        event.processed_at = timezone.now()
        event.save(update_fields=["order", "processed_at", "updated_at"])
    if getattr(link, "auto_accept", True):
        transaction.on_commit(lambda: _auto_accept(order.pk, link.get_platform_display()))
    del inventory_hooks
    return order


def _auto_accept(order_pk, platform_label):
    from apps.orders.services import transition_order

    order = Order.objects.filter(pk=order_pk, status="pending").first()
    if order is not None:
        transition_order(order, "confirmed", notes=f"Auto-accepted ({platform_label})")


def cancel_platform_order(link, order_id: str, *, reason: str = "", event=None) -> Order | None:
    from apps.orders.services import transition_order

    order = Order.objects.filter(restaurant=link.restaurant, source=link.platform, external_id=order_id).first()
    if order is None:
        logger.warning("%s cancel for unknown order %s", link.platform, order_id)
        return None
    if order.status not in ("completed", "cancelled"):
        transition_order(
            order,
            "cancelled",
            notes=f"Cancelled by {link.get_platform_display()}: {reason}",
            cancellation_reason=f"{link.get_platform_display()}: {reason or 'cancelled by platform'}",
        )
    if event is not None:
        event.order = order
        event.processed_at = timezone.now()
        event.save(update_fields=["order", "processed_at", "updated_at"])
    return order


# ── outbound ──────────────────────────────────────────────────────────────


def on_order_status_changed(order, old_status: str, new_status: str) -> None:
    if order.source not in PLATFORM_SOURCES or not order.external_id:
        return
    platform_status = STATUS_MAP.get(new_status)
    if platform_status is None:
        if new_status == "cancelled" and old_status != "cancelled":
            _record_cancel_request(order)
        return
    from apps.delivery import tasks

    enqueue(tasks.sync_status, str(order.pk), platform_status)


def _record_cancel_request(order) -> None:
    """A restaurant cannot cancel a Glovo order through the API; record it so the admin shows a warning."""
    link = link_for(order.restaurant, order.source)
    if link is None:
        return
    DeliveryPlatformEvent.objects.get_or_create(
        link=link,
        event_id=f"{order.source}:{order.external_id}:cancel_requested",
        defaults={"kind": "cancel_requested", "order": order, "payload": {"reason": order.cancellation_reason}},
    )


def push_status(order, platform_status: str, *, session=None) -> DeliveryPlatformEvent | None:
    """Idempotent: one status_pushed event per (order, status). Raises GlovoClientError on retryable failures."""
    from apps.delivery.glovo.client import GlovoClientError, build_client

    link = link_for(order.restaurant, order.source)
    if link is None or not link.is_enabled or not enabled(order.restaurant):
        return None
    event, created = DeliveryPlatformEvent.objects.get_or_create(
        link=link,
        event_id=f"{order.source}:{order.external_id}:{platform_status}",
        defaults={"kind": "status_pushed", "order": order, "payload": {"status": platform_status}},
    )
    if not created and event.processed_at:
        return event
    try:
        response = build_client(link, session=session).set_order_status(order.external_id, platform_status)
    except GlovoClientError as exc:
        if exc.retryable:
            event.error = str(exc)
            event.save(update_fields=["error", "updated_at"])
            raise
        # 400 / 409 on ACCEPTED: the store has no order acceptance (Glovo auto-accepts). Not a failure of ours.
        event.error = f"{exc} (not retried)"
        event.processed_at = timezone.now()
        event.payload = {**event.payload, "response": exc.payload}
        event.save(update_fields=["error", "processed_at", "payload", "updated_at"])
        logger.warning("Glovo rejected %s for order %s: %s", platform_status, order.external_id, exc)
        return event
    event.processed_at = timezone.now()
    event.error = ""
    event.payload = {**event.payload, "response": response}
    event.save(update_fields=["processed_at", "error", "payload", "updated_at"])
    return event


# ── menu ──────────────────────────────────────────────────────────────────


def menu_feed_url(link) -> str:
    from django.conf import settings
    from django.urls import reverse

    base = getattr(settings, "PUBLIC_API_BASE_URL", "").rstrip("/")
    return base + reverse("delivery:glovo-menu", args=[link.pk, link.menu_token])


def start_menu_sync(link, *, by=None):
    from apps.delivery import tasks
    from apps.delivery.models import PlatformMenuSync

    sync = PlatformMenuSync.objects.create(
        link=link, status="queued", triggered_by=by if getattr(by, "is_authenticated", False) else None
    )
    enqueue(tasks.push_menu, str(link.pk), str(sync.pk))
    return sync
