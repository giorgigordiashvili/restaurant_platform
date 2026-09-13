"""
Online ordering rules: is the restaurant taking online orders right now, which
pickup / delivery slots exist, is this address in a zone and what does it cost.
``validate_fulfilment`` is the one check every order-creating path runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.ordering.geo import money, valid_coords
from apps.ordering.models import DeliveryZone, OnlineOrderingSettings
from apps.tenants import hours as H

logger = logging.getLogger(__name__)


class FulfilmentError(ValueError):
    def __init__(self, code: str, message: str = "", **extra):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.extra = extra


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "online_ordering_enabled", False))


def settings_for(restaurant) -> OnlineOrderingSettings:
    cfg = getattr(restaurant, "_ordering_settings_cache", None)
    if cfg is None:
        cfg, _created = OnlineOrderingSettings.objects.get_or_create(
            restaurant=restaurant,
            defaults={"lead_minutes": getattr(restaurant, "average_preparation_time", 30) or 30},
        )
        restaurant._ordering_settings_cache = cfg
    return cfg


def is_paused(cfg: OnlineOrderingSettings, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    return bool(cfg.paused_until and cfg.paused_until > now)


def lead_for(cfg: OnlineOrderingSettings, kind: str) -> int:
    return int(cfg.lead_minutes) + (int(cfg.delivery_extra_minutes) if kind == "delivery" else 0)


# ── slots ──────────────────────────────────────────────────────────────────


def _round_up(dt: datetime, minutes: int) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    rem = dt.minute % minutes
    return dt if rem == 0 else dt + timedelta(minutes=minutes - rem)


def slots(restaurant, kind: str, day: date, *, now: datetime | None = None) -> list[dict]:
    """Time slots a guest may pick for ``day`` (restaurant tz): inside opening hours, after lead time, before cutoff."""
    cfg = settings_for(restaurant)
    if not cfg.scheduling_enabled:
        return []
    now_local = H.local_now(restaurant, now)
    if day < now_local.date() or day > now_local.date() + timedelta(days=cfg.max_days_ahead):
        return []
    interval = max(int(cfg.slot_interval_minutes or 15), 5)
    earliest = _round_up(now_local + timedelta(minutes=lead_for(cfg, kind)), interval)
    out = []
    for iv in H.intervals_for(restaurant, day):
        start = _round_up(max(iv.start, earliest), interval)
        end = iv.end - timedelta(minutes=cfg.cutoff_minutes_before_close)
        t = start
        while t <= end:
            out.append({"time": t.isoformat(), "label": t.strftime("%H:%M")})
            t += timedelta(minutes=interval)
    return out


def asap_ready_at(restaurant, kind: str, *, now: datetime | None = None) -> datetime:
    cfg = settings_for(restaurant)
    return (now or timezone.now()) + timedelta(minutes=lead_for(cfg, kind))


# ── zones & fees ───────────────────────────────────────────────────────────


@dataclass
class Quote:
    zone: DeliveryZone | None
    fee: Decimal
    eta_minutes: int
    min_order: Decimal
    free_over: Decimal
    distance_km: float | None = None

    def as_dict(self) -> dict:
        return {
            "zone_id": str(self.zone.pk) if self.zone else None,
            "zone_name": self.zone.name if self.zone else "",
            "fee": str(self.fee),
            "eta_minutes": self.eta_minutes,
            "min_order": str(self.min_order),
            "free_delivery_over": str(self.free_over),
            "distance_km": round(self.distance_km, 2) if self.distance_km is not None else None,
        }


def zone_for(restaurant, lat, lng) -> DeliveryZone | None:
    for zone in DeliveryZone.objects.filter(restaurant=restaurant, is_active=True).select_related("restaurant"):
        if zone.contains(lat, lng):
            return zone
    return None


def quote_delivery(restaurant, lat, lng, subtotal=Decimal("0")) -> Quote:
    cfg = settings_for(restaurant)
    if not cfg.delivery_enabled:
        raise FulfilmentError("delivery_disabled", "This restaurant does not deliver.")
    if not valid_coords(lat, lng):
        raise FulfilmentError("address_required", "Pin the delivery address on the map.")
    zone = zone_for(restaurant, lat, lng)
    if zone is None:
        raise FulfilmentError("out_of_zone", "Sorry, we do not deliver to this address.")
    fee = money(zone.fee)
    free_over = money(cfg.free_delivery_over)
    if free_over > 0 and money(subtotal) >= free_over:
        fee = Decimal("0.00")
    distance = None
    if restaurant.latitude is not None and restaurant.longitude is not None:
        from apps.ordering.geo import distance_km

        distance = distance_km(restaurant.latitude, restaurant.longitude, lat, lng)
    return Quote(
        zone=zone,
        fee=fee,
        eta_minutes=int(zone.eta_minutes or 45),
        min_order=max(money(zone.min_order), money(cfg.min_order_delivery)),
        free_over=free_over,
        distance_km=distance,
    )


# ── the gate every order path runs ─────────────────────────────────────────


@dataclass
class Fulfilment:
    kind: str  # takeaway | delivery
    scheduled_for: datetime | None
    ready_at: datetime
    fee: Decimal = Decimal("0.00")
    packaging_fee: Decimal = Decimal("0.00")
    zone: DeliveryZone | None = None
    lat: Decimal | None = None
    lng: Decimal | None = None
    address: str = ""
    address_json: dict = field(default_factory=dict)
    instructions: str = ""
    quote: Quote | None = None


def validate_fulfilment(
    restaurant,
    kind: str,
    *,
    subtotal,
    scheduled_for: datetime | None = None,
    lat=None,
    lng=None,
    address: str = "",
    address_json: dict | None = None,
    instructions: str = "",
    now: datetime | None = None,
) -> Fulfilment:
    """
    Raise ``FulfilmentError`` when a takeaway / delivery order cannot be taken;
    otherwise return the fee / time / zone to write on the order.
    Dine-in orders never come here.
    """
    if kind not in ("takeaway", "delivery"):
        raise FulfilmentError("bad_kind", "Unknown order type.")
    cfg = settings_for(restaurant)
    now = now or timezone.now()
    if not enabled(restaurant):
        # Module off: keep the legacy behaviour (takeaway allowed by the coarse flag, no rules).
        if kind == "delivery":
            raise FulfilmentError("delivery_disabled", "This restaurant does not deliver.")
        return Fulfilment(kind=kind, scheduled_for=None, ready_at=asap_ready_at(restaurant, kind, now=now))
    if kind == "takeaway" and not cfg.pickup_enabled:
        raise FulfilmentError("pickup_disabled", "Pickup orders are switched off.")
    if kind == "delivery" and not cfg.delivery_enabled:
        raise FulfilmentError("delivery_disabled", "This restaurant does not deliver.")
    if is_paused(cfg, now):
        raise FulfilmentError(
            "paused",
            cfg.pause_reason or "Online orders are paused for a moment. Please try again soon.",
            resume_at=cfg.paused_until.isoformat(),
        )

    subtotal = money(subtotal)
    minimum = money(cfg.min_order_pickup if kind == "takeaway" else cfg.min_order_delivery)

    # -- when
    if scheduled_for is not None:
        if not cfg.scheduling_enabled:
            raise FulfilmentError("slot_invalid", "This restaurant only takes orders for now.")
        if timezone.is_naive(scheduled_for):
            scheduled_for = timezone.make_aware(scheduled_for, H.tz(restaurant))
        local = scheduled_for.astimezone(H.tz(restaurant))
        allowed = {s["time"] for s in slots(restaurant, kind, local.date(), now=now)}
        if local.isoformat() not in allowed:
            raise FulfilmentError("slot_invalid", "That time is no longer available. Pick another slot.")
        ready_at = scheduled_for
    else:
        if not cfg.asap_enabled:
            raise FulfilmentError("slot_required", "Please choose a pickup time.")
        iv = H.interval_at(restaurant, now)
        if iv is None:
            nxt = H.next_opening(restaurant, now)
            raise FulfilmentError(
                "closed",
                "We are closed right now.",
                next_opening=nxt.isoformat() if nxt else None,
            )
        cutoff = iv.end - timedelta(minutes=cfg.cutoff_minutes_before_close)
        if H.local_now(restaurant, now) > cutoff:
            nxt = H.next_opening(restaurant, now)
            raise FulfilmentError(
                "closing_soon",
                "The kitchen has stopped taking online orders for today.",
                next_opening=nxt.isoformat() if nxt else None,
            )
        ready_at = asap_ready_at(restaurant, kind, now=now)

    result = Fulfilment(
        kind=kind,
        scheduled_for=scheduled_for,
        ready_at=ready_at,
        packaging_fee=money(cfg.packaging_fee),
        address=address or "",
        address_json=address_json or {},
        instructions=(instructions or "")[:300],
    )

    # -- where
    if kind == "delivery":
        q = quote_delivery(restaurant, lat, lng, subtotal)
        minimum = max(minimum, q.min_order)
        result.quote = q
        result.zone = q.zone
        result.fee = q.fee
        result.lat = Decimal(str(lat)).quantize(Decimal("0.000001"))
        result.lng = Decimal(str(lng)).quantize(Decimal("0.000001"))
        if not (address or "").strip():
            raise FulfilmentError("address_required", "Delivery address is required for delivery orders.")

    if minimum > 0 and subtotal < minimum:
        raise FulfilmentError(
            "min_order",
            f"Minimum order for {'delivery' if kind == 'delivery' else 'pickup'} is {minimum}.",
            minimum=str(minimum),
            missing=str(minimum - subtotal),
        )
    return result


def apply_fulfilment(order, f: Fulfilment) -> None:
    """Write the validated fulfilment onto the order (caller recalculates totals)."""
    order.scheduled_for = f.scheduled_for
    order.estimated_ready_at = f.ready_at
    order.delivery_fee = f.fee
    order.packaging_fee = f.packaging_fee
    order.delivery_zone = f.zone
    order.delivery_lat = f.lat
    order.delivery_lng = f.lng
    order.delivery_instructions = f.instructions
    if f.address:
        order.delivery_address = f.address
    if f.address_json:
        order.address_json = f.address_json
    order.save(
        update_fields=[
            "scheduled_for",
            "estimated_ready_at",
            "delivery_fee",
            "packaging_fee",
            "delivery_zone",
            "delivery_lat",
            "delivery_lng",
            "delivery_instructions",
            "delivery_address",
            "address_json",
            "updated_at",
        ]
    )


# ── public config ──────────────────────────────────────────────────────────


def public_config(restaurant, *, now: datetime | None = None) -> dict:
    cfg = settings_for(restaurant)
    now = now or timezone.now()
    open_now = H.is_open_at(restaurant, now)
    nxt = H.next_opening(restaurant, now)
    closes = H.closes_at(restaurant, now)
    on = enabled(restaurant) and bool(restaurant.accepts_remote_orders)
    return {
        "enabled": on,
        "pickup": on and cfg.pickup_enabled and bool(restaurant.accepts_takeaway),
        "delivery": on and cfg.delivery_enabled and bool(restaurant.accepts_takeaway),
        "asap": cfg.asap_enabled,
        "scheduling": cfg.scheduling_enabled,
        "open_now": open_now,
        "paused": is_paused(cfg, now),
        "pause_reason": cfg.pause_reason if is_paused(cfg, now) else "",
        "resume_at": cfg.paused_until.isoformat() if is_paused(cfg, now) else None,
        "next_opening": nxt.isoformat() if nxt else None,
        "closes_at": closes.isoformat() if closes else None,
        "lead_minutes": int(cfg.lead_minutes),
        "delivery_lead_minutes": lead_for(cfg, "delivery"),
        "min_order_pickup": str(money(cfg.min_order_pickup)),
        "min_order_delivery": str(money(cfg.min_order_delivery)),
        "free_delivery_over": str(money(cfg.free_delivery_over)),
        "packaging_fee": str(money(cfg.packaging_fee)),
        "max_days_ahead": int(cfg.max_days_ahead),
        "restaurant_location": (
            {"lat": float(restaurant.latitude), "lng": float(restaurant.longitude)}
            if restaurant.latitude is not None and restaurant.longitude is not None
            else None
        ),
        "zones": [
            {
                "id": str(z.pk),
                "name": z.name,
                "kind": z.kind,
                "radius_km": float(z.radius_km),
                "polygon": z.polygon if z.kind == "polygon" else [],
                "fee": str(money(z.fee)),
                "min_order": str(money(z.min_order)),
                "eta_minutes": z.eta_minutes,
                "color": z.color,
            }
            for z in DeliveryZone.objects.filter(restaurant=restaurant, is_active=True)
        ],
    }


def pause(restaurant, minutes: int, reason: str = "", *, by=None) -> OnlineOrderingSettings:
    cfg = settings_for(restaurant)
    cfg.paused_until = timezone.now() + timedelta(minutes=max(int(minutes), 1))
    cfg.pause_reason = (reason or "")[:120]
    cfg.save(update_fields=["paused_until", "pause_reason", "updated_at"])
    return cfg


def resume(restaurant, *, by=None) -> OnlineOrderingSettings:
    cfg = settings_for(restaurant)
    cfg.paused_until = None
    cfg.pause_reason = ""
    cfg.save(update_fields=["paused_until", "pause_reason", "updated_at"])
    return cfg


def summary(restaurant, *, now: datetime | None = None) -> dict:
    """Dashboard card numbers."""
    from django.db.models import Count

    from apps.orders.models import Order

    from .models import Delivery, RestaurantDomain

    now = now or timezone.now()
    today = H.local_now(restaurant, now).date()
    start = datetime.combine(today, datetime.min.time(), tzinfo=H.tz(restaurant))
    qs = Order.objects.filter(
        restaurant=restaurant, source="web", order_type__in=("takeaway", "delivery"), created_at__gte=start
    ).exclude(status__in=("pending_payment", "cancelled"))
    by_type = {r["order_type"]: r["n"] for r in qs.values("order_type").annotate(n=Count("id"))}
    return {
        "pickup_today": by_type.get("takeaway", 0),
        "delivery_today": by_type.get("delivery", 0),
        "in_flight": Delivery.objects.filter(restaurant=restaurant, status__in=Delivery.OPEN).count(),
        "failed": Delivery.objects.filter(restaurant=restaurant, status="failed", created_at__gte=start).count(),
        "unverified_domains": RestaurantDomain.objects.filter(restaurant=restaurant, verified_at__isnull=True).count(),
        "paused": is_paused(settings_for(restaurant), now),
    }
