"""
Warehouse periodic jobs (schedules seeded by inventory/0003_seed_beat).

All jobs iterate only restaurants with ``warehouse_enabled`` and do their
work per restaurant in a transaction, so one bad tenant never blocks the
rest.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from celery import shared_task

from apps.inventory import services
from apps.inventory.models import InventoryAlert, OrderStockReservation, StockLot, fmt_qty
from apps.tenants.models import Restaurant

logger = logging.getLogger(__name__)

# How long a prepaid checkout may sit unpaid before its ingredient hold is
# given back to other customers.
PAYMENT_RESERVATION_TTL_MINUTES = 30


def _enabled_restaurants():
    return Restaurant.objects.filter(is_active=True, warehouse_enabled=True)


@shared_task(name="inventory.expire_lots_nightly", ignore_result=True)
def expire_lots_nightly() -> dict[str, int]:
    """Write off every lot past its expiry date and re-check availability."""
    today = timezone.localdate()
    written_off = 0
    for restaurant in _enabled_restaurants():
        lot_ids = list(
            StockLot.objects.filter(
                restaurant=restaurant, expiry_date__lt=today, remaining_qty__gt=0, is_expired_out=False
            ).values_list("id", flat=True)
        )
        for lot_id in lot_ids:
            with transaction.atomic():
                lot = StockLot.objects.select_for_update(skip_locked=True).filter(pk=lot_id).first()
                if lot is None or lot.remaining_qty <= 0:
                    continue
                services.record_expired_lot(lot)
                services.open_alert(
                    restaurant.pk,
                    "expired",
                    f"{lot.stock_item.name}: lot of {fmt_qty(lot.received_qty, lot.stock_item.base_unit)} expired "
                    f"{lot.expiry_date} and was written off.",
                    lot=lot,
                    stock_item=lot.stock_item,
                )
                written_off += 1
    if written_off:
        logger.info("Expired lots written off: %s", written_off)
    return {"written_off": written_off}


@shared_task(name="inventory.expiring_soon_alerts", ignore_result=True)
def expiring_soon_alerts() -> dict[str, int]:
    opened = 0
    for restaurant in _enabled_restaurants():
        for lot in services.expiring_report(restaurant):
            days = lot.days_to_expiry
            when = "today" if days == 0 else (f"in {days} day(s)" if days > 0 else f"{-days} day(s) ago")
            _, created = services.open_alert(
                restaurant.pk,
                "expiring_soon",
                f"{lot.stock_item.name}: {fmt_qty(lot.remaining_qty, lot.stock_item.base_unit)} expires {when} "
                f"({lot.expiry_date}).",
                lot=lot,
                stock_item=lot.stock_item,
                payload={"days": days},
            )
            opened += int(created)
    return {"opened": opened}


@shared_task(name="inventory.low_stock_nightly", ignore_result=True)
def low_stock_nightly() -> dict[str, int]:
    """Refresh low-stock alerts and announce tomorrow's buy list."""
    tomorrow = timezone.localdate() + timezone.timedelta(days=1)
    lists = 0
    for restaurant in _enabled_restaurants():
        services.recompute_availability(restaurant.pk)
        lines = services.buy_list(restaurant, for_date=tomorrow)
        # One "buy list" alert per day: resolve yesterday's before opening today's.
        for alert in InventoryAlert.objects.filter(restaurant=restaurant, kind="buy_list", status="open"):
            alert.resolve(reason="auto")
        if lines:
            total = sum(line.estimated_cost for line in lines)
            InventoryAlert.objects.create(
                restaurant=restaurant,
                kind="buy_list",
                message=f"Buy list for {tomorrow}: {len(lines)} item(s), about {total} {restaurant.default_currency}.",
                dedupe_key=f"buy_list:{tomorrow}",
                payload={"for_date": str(tomorrow), "lines": len(lines), "estimated_total": str(total)},
            )
            lists += 1
    return {"buy_lists": lists}


@shared_task(name="inventory.release_stale_payment_reservations", ignore_result=True)
def release_stale_payment_reservations() -> dict[str, int]:
    """Give back holds on prepaid checkouts nobody finished."""
    cutoff = timezone.now() - timezone.timedelta(minutes=PAYMENT_RESERVATION_TTL_MINUTES)
    released = 0
    stale = OrderStockReservation.objects.filter(
        status=OrderStockReservation.STATUS_RESERVED, order__status="pending_payment", created_at__lt=cutoff
    ).select_related("order")
    for res in stale:
        if services.release_reservation(res.order):
            released += 1
    return {"released": released}


@shared_task(name="inventory.reconcile_order_reservations", ignore_result=True)
def reconcile_order_reservations() -> dict[str, int]:
    """
    Safety net for any status change that bypassed the hooks: reservations
    whose order moved on get consumed / released / restored. Idempotent.
    """
    consumed = released = restored = 0
    reserved = OrderStockReservation.objects.filter(status=OrderStockReservation.STATUS_RESERVED).select_related(
        "order", "restaurant"
    )
    for res in reserved:
        if not res.restaurant.warehouse_enabled:
            continue
        if res.order.status in services.CONSUMING_STATUSES:
            consumed += int(services.consume_order(res.order))
        elif res.order.status == "cancelled":
            released += int(services.release_reservation(res.order))
    consumed_cancelled = OrderStockReservation.objects.filter(
        status=OrderStockReservation.STATUS_CONSUMED, order__status="cancelled"
    ).select_related("order")
    for res in consumed_cancelled:
        restored += int(services.restore_order(res.order))
    return {"consumed": consumed, "released": released, "restored": restored}
