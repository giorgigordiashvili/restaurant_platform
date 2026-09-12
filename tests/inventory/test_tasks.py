"""Periodic jobs and the beat rows that drive them."""

from decimal import Decimal

from django.utils import timezone

import pytest

from apps.inventory import services, tasks
from apps.inventory.models import InventoryAlert, OrderStockReservation, StockLot

pytestmark = pytest.mark.django_db


def test_expire_lots_nightly_writes_off_and_alerts(flour, lots):
    lot_a, _ = lots
    StockLot.objects.filter(pk=lot_a.pk).update(expiry_date=timezone.localdate() - timezone.timedelta(days=1))
    assert tasks.expire_lots_nightly() == {"written_off": 1}
    lot_a.refresh_from_db()
    assert lot_a.remaining_qty == 0 and lot_a.is_expired_out
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("1000")
    assert InventoryAlert.objects.filter(kind="expired", lot=lot_a, status="open").exists()
    assert tasks.expire_lots_nightly() == {"written_off": 0}


def test_expiring_soon_alerts_dedupe_by_lot(flour, lots):
    assert tasks.expiring_soon_alerts() == {"opened": 1}
    assert tasks.expiring_soon_alerts() == {"opened": 0}


def test_low_stock_nightly_publishes_buy_list(flour, lots):
    assert tasks.low_stock_nightly() == {"buy_lists": 1}
    alert = InventoryAlert.objects.get(kind="buy_list", status="open")
    assert alert.payload["lines"] == 1
    tasks.low_stock_nightly()
    assert InventoryAlert.objects.filter(kind="buy_list", status="open").count() == 1


def test_release_stale_payment_reservations(order_factory, pizza, flour, lots):
    order = order_factory([(pizza, 1, [])], status="pending_payment")
    res = services.reserve_for_order(order)
    assert tasks.release_stale_payment_reservations() == {"released": 0}
    OrderStockReservation.objects.filter(pk=res.pk).update(created_at=timezone.now() - timezone.timedelta(hours=1))
    assert tasks.release_stale_payment_reservations() == {"released": 1}
    flour.refresh_from_db()
    assert flour.reserved_qty == 0


def test_reconcile_catches_missed_transitions(order_factory, pizza, flour, lots):
    accepted = order_factory([(pizza, 1, [])])
    cancelled = order_factory([(pizza, 1, [])])
    consumed_then_cancelled = order_factory([(pizza, 1, [])])
    for o in (accepted, cancelled, consumed_then_cancelled):
        services.reserve_for_order(o)
    services.consume_order(consumed_then_cancelled)
    # statuses changed behind the hooks' back
    type(accepted).objects.filter(pk=accepted.pk).update(status="preparing")
    type(cancelled).objects.filter(pk__in=[cancelled.pk, consumed_then_cancelled.pk]).update(status="cancelled")
    assert tasks.reconcile_order_reservations() == {"consumed": 1, "released": 1, "restored": 1}
    flour.refresh_from_db()
    assert flour.reserved_qty == 0 and flour.on_hand_qty == Decimal("1300")


def test_beat_rows_seeded(db):
    from django_celery_beat.models import PeriodicTask

    names = set(PeriodicTask.objects.filter(task__startswith="inventory.").values_list("task", flat=True))
    assert names == {
        "inventory.expire_lots_nightly",
        "inventory.expiring_soon_alerts",
        "inventory.low_stock_nightly",
        "inventory.release_stale_payment_reservations",
        "inventory.reconcile_order_reservations",
    }
