"""
Platform orders in, status updates out -- per platform.

Inbound (webhook -> ``create_platform_order``): an ``Order`` with
``source=<platform>``, lines at the platform's prices, non-strict stock
reservation, then (when ``auto_accept``) the same confirm the POS "Accept"
button does. Outbound (``on_order_status_changed``): our status -> the
platform's call (Glovo ``ACCEPTED`` / ``READY_FOR_PICKUP``; Wolt ``accept`` /
``confirm-preorder`` / ``ready`` / ``delivered`` / ``reject``), pushed by a
Celery task with retries. Menu push, item refunds and store pause / resume
live here too so admin, POS and tasks share one code path.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.enqueue import enqueue
from apps.delivery.errors import PlatformClientError
from apps.delivery.ids import parse_attribute_id, parse_product_id
from apps.delivery.models import PLATFORM_SOURCES, DeliveryPlatformEvent, RestaurantDeliveryPlatform
from apps.orders.models import Order, OrderItem, OrderItemModifier, OrderStatusHistory

logger = logging.getLogger(__name__)

GLOVO_STATUS_MAP = {"confirmed": "ACCEPTED", "ready": "READY_FOR_PICKUP"}
STATUS_MAP = GLOVO_STATUS_MAP  # backwards-compatible name
TOTAL_TOLERANCE = Decimal("0.05")
IMPLEMENTED = ("glovo", "wolt")


class DeliveryError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


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


def client_for(link, *, session=None):
    """The platform's HTTP client (tests monkeypatch the per-platform ``build_client``)."""
    if link.platform == "glovo":
        from apps.delivery.glovo.client import build_client

        return build_client(link, session=session)
    if link.platform == "wolt":
        from apps.delivery.wolt.client import build_client

        return build_client(link, session=session)
    raise DeliveryError("not_implemented", f"{link.get_platform_display()} has no API integration yet.")


def is_configured(link) -> bool:
    from apps.delivery.config import NotConfigured, resolve_glovo_config, resolve_wolt_config

    try:
        if link.platform == "glovo":
            resolve_glovo_config(link)
        elif link.platform == "wolt":
            resolve_wolt_config(link)
        else:
            return False
    except NotConfigured:
        return False
    return True


# ── inbound ───────────────────────────────────────────────────────────────


def create_platform_order(link, parsed, *, event=None, auto_accept: bool | None = None) -> Order:
    """Runs inside the caller's transaction (the webhook / task holds the event row lock)."""
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
    if parsed.is_preorder and parsed.preorder_time:
        notes.append(f"Pre-order for {timezone.localtime(parsed.preorder_time):%d.%m %H:%M}")
    prep = getattr(link, "prep_time_minutes", 20) or 20
    eta = parsed.estimated_pickup_time or parsed.preorder_time or (timezone.now() + timezone.timedelta(minutes=prep))
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
        estimated_ready_at=eta,
        platform_data={
            "order_code": parsed.order_code,
            "pickup_eta": parsed.estimated_pickup_time.isoformat() if parsed.estimated_pickup_time else None,
            "courier": {"name": parsed.courier_name, "phone": parsed.courier_phone},
            "payment_method": parsed.payment_method,
            "currency": parsed.currency,
            "raw_total_minor": str(parsed.total_customer_to_pay),
            "is_pickup_by_customer": parsed.is_picked_up_by_customer,
            "delivery_type": parsed.delivery_type,
            "self_delivery": parsed.self_delivery,
            "is_preorder": parsed.is_preorder,
            "preorder_time": parsed.preorder_time.isoformat() if parsed.preorder_time else None,
            "platform_status": parsed.platform_status,
            "extra": parsed.extra,
            "lines": {},
            "raw": parsed.raw,
        },
    )
    lines = {}
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
        if p.line_id:
            lines[str(item.pk)] = p.line_id
        for a in p.attributes:
            mpk = parse_attribute_id(a.id)
            modifier = Modifier.objects.filter(pk=mpk, group__restaurant=restaurant).first() if mpk else None
            OrderItemModifier.objects.create(
                order_item=item,
                modifier=modifier,
                modifier_name=a.name or "Option",
                price_adjustment=a.price * max(a.quantity, 1),
            )
        item.recalculate_total()  # payload prices, never MenuItem.price
    if lines:
        order.platform_data["lines"] = lines
        order.save(update_fields=["platform_data", "updated_at"])
    order.calculate_totals()
    expected = parsed.estimated_total_price or parsed.total_customer_to_pay
    if expected and abs(order.total - expected) > TOTAL_TOLERANCE:
        order.platform_data["total_mismatch"] = {"ours": str(order.total), "platform": str(expected)}
        order.save(update_fields=["platform_data", "updated_at"])
        logger.warning(
            "%s order %s total mismatch: ours %s platform %s", link.platform, parsed.order_id, order.total, expected
        )
    currency = getattr(restaurant, "default_currency", "GEL")
    if parsed.currency and currency and parsed.currency != currency:
        logger.warning(
            "%s order %s currency %s differs from restaurant %s",
            link.platform,
            parsed.order_id,
            parsed.currency,
            currency,
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
    if auto_accept is None:
        auto_accept = bool(getattr(link, "auto_accept", True))
    if auto_accept:
        transaction.on_commit(lambda: _auto_accept(order.pk, link.get_platform_display()))
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


# ── Wolt notifications ────────────────────────────────────────────────────


def handle_wolt_notification(link, note, *, event) -> None:
    """Runs inside the webhook's transaction; the order payload itself is fetched by a task."""
    from apps.delivery import tasks
    from apps.orders.services import transition_order

    order = Order.objects.filter(restaurant=link.restaurant, source="wolt", external_id=note.order_id).first()
    event.order = order
    if order is None:
        if note.status in ("REJECTED", "DELIVERED"):
            event.processed_at = timezone.now()
            event.error = "unknown order"
            event.save(update_fields=["order", "processed_at", "error", "updated_at"])
            return
        event.save(update_fields=["order", "updated_at"])
        enqueue(tasks.wolt_fetch_order, str(link.pk), note.order_id, str(event.pk))
        return
    if note.status == "REJECTED":
        cancel_platform_order(link, note.order_id, reason="rejected / cancelled on Wolt", event=event)
        return
    if note.status == "DELIVERED":
        if order.status not in ("completed", "cancelled"):
            transition_order(order, "completed", notes="Delivered (Wolt)")
    elif note.status == "PRODUCTION":
        # Pre-order: Wolt says it is time to cook. Instant orders are already in the kitchen by now.
        if order.status == "pending" and getattr(link, "auto_accept", True):
            transaction.on_commit(lambda: _auto_accept(order.pk, "Wolt"))
    order.platform_data = {**order.platform_data, "platform_status": note.status.lower()}
    order.save(update_fields=["platform_data", "updated_at"])
    event.processed_at = timezone.now()
    event.save(update_fields=["order", "processed_at", "updated_at"])


def fetch_wolt_order(link, order_id: str, *, event=None, session=None) -> Order | None:
    """Pull ``GET /orders/{id}`` and create the local order (idempotent on external_id)."""
    from apps.delivery.wolt.orders import parse_order

    existing = Order.objects.filter(restaurant=link.restaurant, source="wolt", external_id=order_id).first()
    if existing is not None:
        if event is not None and not event.processed_at:
            event.order = existing
            event.processed_at = timezone.now()
            event.save(update_fields=["order", "processed_at", "updated_at"])
        return existing
    payload = client_for(link, session=session).get_order(order_id)
    parsed = parse_order(payload)
    if parsed.platform_status in ("rejected", "refunded"):
        if event is not None:
            event.processed_at = timezone.now()
            event.error = f"order already {parsed.platform_status}"
            event.save(update_fields=["processed_at", "error", "updated_at"])
        return None
    auto = bool(getattr(link, "auto_accept", True))
    with transaction.atomic():
        order = create_platform_order(link, parsed, event=event, auto_accept=auto and not parsed.is_preorder)
        if auto and parsed.is_preorder:
            # Confirm the pre-order now; the kitchen starts on Wolt's PRODUCTION notification.
            from apps.delivery import tasks

            enqueue(tasks.sync_status, str(order.pk), "confirm-preorder")
    return order


# ── outbound ──────────────────────────────────────────────────────────────


def platform_action(order, old_status: str, new_status: str) -> str | None:
    """Our transition -> the platform's call name (None = nothing to send)."""
    pd = order.platform_data or {}
    if order.source == "glovo":
        return GLOVO_STATUS_MAP.get(new_status)
    if order.source == "wolt":
        if new_status == "confirmed":
            return "confirm-preorder" if pd.get("is_preorder") else "accept"
        if new_status == "ready":
            return "ready"
        if new_status == "completed":
            return "delivered" if pd.get("is_pickup_by_customer") or pd.get("self_delivery") else None
        if new_status == "cancelled" and old_status in ("pending", "pending_payment"):
            return "reject"
    return None


def on_order_status_changed(order, old_status: str, new_status: str) -> None:
    if order.source not in PLATFORM_SOURCES or not order.external_id:
        return
    action = platform_action(order, old_status, new_status)
    if action is None:
        if new_status == "cancelled" and old_status != "cancelled":
            _record_cancel_request(order)
        return
    from apps.delivery import tasks

    enqueue(tasks.sync_status, str(order.pk), action)


def _record_cancel_request(order) -> None:
    """Neither Glovo nor (after acceptance) Wolt lets the restaurant cancel through the API; record it for the admin."""
    link = link_for(order.restaurant, order.source)
    if link is None:
        return
    DeliveryPlatformEvent.objects.get_or_create(
        link=link,
        event_id=f"{order.source}:{order.external_id}:cancel_requested",
        defaults={"kind": "cancel_requested", "order": order, "payload": {"reason": order.cancellation_reason}},
    )


def _send_status(link, order, action: str, *, session=None) -> dict:
    client = client_for(link, session=session)
    if link.platform == "glovo":
        return client.set_order_status(order.external_id, action)
    if action == "accept":
        eta = order.estimated_ready_at.isoformat() if order.estimated_ready_at else None
        if (order.platform_data or {}).get("self_delivery"):
            return client.self_delivery_accept(order.external_id, adjusted_pickup_time=eta)
        return client.accept(order.external_id, adjusted_pickup_time=eta)
    if action == "confirm-preorder":
        return client.confirm_preorder(order.external_id)
    if action == "ready":
        return client.ready(order.external_id)
    if action == "delivered":
        return client.delivered(order.external_id)
    if action == "reject":
        return client.reject(order.external_id, order.cancellation_reason or "Restaurant rejected the order")
    raise DeliveryError("unknown_action", action)


def push_status(order, platform_status: str, *, session=None) -> DeliveryPlatformEvent | None:
    """Idempotent: one status_pushed event per (order, action). Raises PlatformClientError on retryable failures."""
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
        response = _send_status(link, order, platform_status, session=session)
    except PlatformClientError as exc:
        if exc.retryable:
            event.error = str(exc)
            event.save(update_fields=["error", "updated_at"])
            raise
        # 400 / 409: e.g. Glovo store without order acceptance, or Wolt already moved on. Not a failure of ours.
        event.error = f"{exc} (not retried)"
        event.processed_at = timezone.now()
        event.payload = {**event.payload, "response": exc.payload}
        event.save(update_fields=["error", "processed_at", "payload", "updated_at"])
        logger.warning("%s rejected %s for order %s: %s", link.platform, platform_status, order.external_id, exc)
        return event
    event.processed_at = timezone.now()
    event.error = ""
    event.payload = {**event.payload, "response": response}
    event.save(update_fields=["processed_at", "error", "payload", "updated_at"])
    return event


# ── refunds (Wolt) ────────────────────────────────────────────────────────


def on_order_item_voided(item) -> None:
    """A voided line on an accepted Wolt order becomes a partial refund on Wolt."""
    order = item.order
    if order.source != "wolt" or not order.external_id:
        return
    line_id = ((order.platform_data or {}).get("lines") or {}).get(str(item.pk))
    if not line_id or order.status in ("pending", "pending_payment", "cancelled"):
        return
    from apps.delivery import tasks

    enqueue(tasks.refund_items, str(order.pk), [{"id": line_id, "count": int(item.quantity)}], str(item.pk))


def push_refund(order, items: list[dict], ref: str, *, session=None) -> DeliveryPlatformEvent | None:
    link = link_for(order.restaurant, order.source)
    if link is None or not link.is_enabled or not enabled(order.restaurant):
        return None
    event, created = DeliveryPlatformEvent.objects.get_or_create(
        link=link,
        event_id=f"{order.source}:{order.external_id}:refund:{ref}",
        defaults={"kind": "refund_pushed", "order": order, "payload": {"items": items}},
    )
    if not created and event.processed_at:
        return event
    try:
        response = client_for(link, session=session).refund_items(order.external_id, items)
    except PlatformClientError as exc:
        event.error = str(exc) if exc.retryable else f"{exc} (not retried)"
        if not exc.retryable:
            event.processed_at = timezone.now()
            event.payload = {**event.payload, "response": exc.payload}
        event.save(update_fields=["error", "processed_at", "payload", "updated_at"])
        if exc.retryable:
            raise
        return event
    event.processed_at = timezone.now()
    event.payload = {**event.payload, "response": response}
    event.save(update_fields=["processed_at", "payload", "updated_at"])
    return event


# ── store open / close ────────────────────────────────────────────────────


def pause_store(link, minutes: int, *, by=None, session=None) -> DeliveryPlatformEvent:
    """Hide the store on the platform for ``minutes`` (kitchen swamped). Synchronous: the admin / POS waits."""
    minutes = max(5, min(int(minutes or 30), 24 * 60))
    until = timezone.now() + timezone.timedelta(minutes=minutes)
    until_iso = until.replace(microsecond=0).isoformat()
    client = client_for(link, session=session)
    try:
        response = (
            client.close_store(until_iso) if link.platform == "glovo" else client.set_online(False, until_iso=until_iso)
        )
    except PlatformClientError as exc:
        raise DeliveryError("platform_error", str(exc)) from exc
    RestaurantDeliveryPlatform.objects.filter(pk=link.pk).update(store_paused_until=until)
    link.store_paused_until = until
    return DeliveryPlatformEvent.objects.create(
        link=link,
        event_id=f"{link.platform}:store:paused:{int(until.timestamp())}",
        kind="store_status",
        payload={"status": "paused", "until": until_iso, "by": str(getattr(by, "pk", "") or ""), "response": response},
        processed_at=timezone.now(),
    )


def resume_store(link, *, by=None, session=None) -> DeliveryPlatformEvent:
    client = client_for(link, session=session)
    try:
        response = client.open_store() if link.platform == "glovo" else client.set_online(True)
    except PlatformClientError as exc:
        raise DeliveryError("platform_error", str(exc)) from exc
    RestaurantDeliveryPlatform.objects.filter(pk=link.pk).update(store_paused_until=None)
    link.store_paused_until = None
    return DeliveryPlatformEvent.objects.create(
        link=link,
        event_id=f"{link.platform}:store:resumed:{int(timezone.now().timestamp())}",
        kind="store_status",
        payload={"status": "online", "by": str(getattr(by, "pk", "") or ""), "response": response},
        processed_at=timezone.now(),
    )


def store_status(link, *, session=None) -> dict:
    """What we know (and, for Wolt, what the platform says) about the store being visible."""
    paused = link.store_paused_until
    if paused and paused <= timezone.now():
        paused = None
    out = {"paused_until": paused.isoformat() if paused else None, "online": paused is None, "live": None}
    if link.platform == "wolt" and link.is_enabled and is_configured(link):
        try:
            live = client_for(link, session=session).venue_status()
            status = live.get("status") or {}
            out["live"] = {
                "is_online": status.get("is_online"),
                "is_open": status.get("is_open"),
                "last_orders": live.get("last_three_orders_status"),
            }
            if status.get("is_online") is not None:
                out["online"] = bool(status.get("is_online"))
        except (PlatformClientError, DeliveryError) as exc:
            out["live"] = {"error": str(exc)}
    return out


# ── menu ──────────────────────────────────────────────────────────────────


def menu_feed_url(link) -> str:
    from django.conf import settings
    from django.urls import reverse

    base = getattr(settings, "PUBLIC_API_BASE_URL", "").rstrip("/")
    return base + reverse("delivery:glovo-menu", args=[link.pk, link.menu_token])


def start_menu_sync(link, *, by=None, kind: str = "full"):
    """Queue a full menu push (``full``) or a prices + availability refresh (``updates``)."""
    from apps.delivery import tasks
    from apps.delivery.models import PlatformMenuSync

    sync = PlatformMenuSync.objects.create(
        link=link,
        status="queued",
        request={"kind": kind},
        triggered_by=by if getattr(by, "is_authenticated", False) else None,
    )
    enqueue(tasks.push_menu_updates if kind == "updates" else tasks.push_menu, str(link.pk), str(sync.pk))
    return sync
