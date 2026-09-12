"""Warehouse services: reservation lifecycle, FIFO lots, stock ops, availability, reports."""

from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import pytest

from apps.inventory import services
from apps.inventory.exceptions import InsufficientStock, UnitMismatch
from apps.inventory.models import (
    EmployeeMeal,
    InventoryAlert,
    OrderStockReservation,
    RestaurantDeliveryPlatform,
    StockAdjustment,
    StockItem,
    StockLot,
    StockMovement,
    WasteEntry,
)

pytestmark = pytest.mark.django_db


def _cache_matches_ledger(restaurant):
    assert services.rebuild_stock_cache(restaurant) == []


# ── reserve ───────────────────────────────────────────────────────────────


def test_reserve_holds_ingredients(order_factory, pizza, flour, lots, wh):
    order = order_factory([(pizza, 2, [])])
    res = services.reserve_for_order(order)
    flour.refresh_from_db()
    assert res.status == "reserved"
    assert flour.reserved_qty == Decimal("400")
    assert flour.on_hand_qty == Decimal("1500")
    assert flour.available_qty == Decimal("1100")
    assert res.lines.count() == 1
    _cache_matches_ledger(wh)


def test_reserve_counts_modifier_recipe_per_portion(order_factory, pizza, extra_cheese, cheese, flour, lots, units):
    services.receive_stock(cheese, "1", units["kg"])
    order = order_factory([(pizza, 3, [extra_cheese])])
    services.reserve_for_order(order)
    cheese.refresh_from_db()
    assert cheese.reserved_qty == Decimal("150")


def test_reserve_shortfall_raises_with_dish_details(order_factory, pizza, flour, lots):
    order = order_factory([(pizza, 8, [])])  # needs 1600 g, only 1500 on hand
    with pytest.raises(InsufficientStock) as exc:
        services.reserve_for_order(order)
    short = exc.value.shortfalls
    assert short[0]["name"] == "Pizza"
    assert short[0]["available_portions"] == 7
    assert short[0]["stock_item_name"] == "Flour"
    assert exc.value.status_code == 409
    flour.refresh_from_db()
    assert flour.reserved_qty == 0
    assert not OrderStockReservation.objects.filter(order=order).exists()


def test_reserve_is_idempotent(order_factory, pizza, flour, lots):
    order = order_factory([(pizza, 1, [])])
    services.reserve_for_order(order)
    services.reserve_for_order(order)
    flour.refresh_from_db()
    assert flour.reserved_qty == Decimal("200")
    assert OrderStockReservation.objects.filter(order=order).count() == 1


def test_untracked_dish_needs_nothing(order_factory, salad, flour, lots):
    order = order_factory([(salad, 5, [])])
    res = services.reserve_for_order(order)
    assert res.lines.count() == 0
    flour.refresh_from_db()
    assert flour.reserved_qty == 0


def test_inactive_stock_item_is_ignored(order_factory, pizza, flour, lots):
    flour.is_active = False
    flour.save()
    order = order_factory([(pizza, 100, [])])
    services.reserve_for_order(order)  # no error: recipe line skipped
    assert services.available_portions(pizza) is None


def test_feature_off_is_a_noop(order_factory, pizza, flour, lots, wh):
    wh.warehouse_enabled = False
    wh.save()
    order = order_factory([(pizza, 1, [])])
    assert services.reserve_for_order(order) is None
    assert not OrderStockReservation.objects.exists()


def test_reserve_locks_items_in_pk_order(order_factory, pizza, extra_cheese, cheese, flour, lots, units):
    services.receive_stock(cheese, "1", units["kg"])
    order = order_factory([(pizza, 1, [extra_cheese])])
    with CaptureQueriesContext(connection) as ctx:
        services.reserve_for_order(order)
    locks = [q["sql"] for q in ctx.captured_queries if "FOR UPDATE" in q["sql"] and "inventory_stock_items" in q["sql"]]
    assert locks and all("ORDER BY" in sql for sql in locks)


def test_available_portions(pizza, flour, lots):
    assert services.available_portions(pizza) == 7


# ── consume / release / restore ───────────────────────────────────────────


def test_consume_takes_fifo_by_expiry_with_lot_costs(order_factory, pizza, flour, lots, wh):
    lot_a, lot_b = lots
    order = order_factory([(pizza, 3, [])])  # 600 g: 500 from A (2/kg) + 100 from B (3/kg)
    services.reserve_for_order(order)
    assert services.consume_order(order) is True
    lot_a.refresh_from_db()
    lot_b.refresh_from_db()
    assert lot_a.remaining_qty == 0
    assert lot_b.remaining_qty == Decimal("900")
    moves = list(StockMovement.objects.filter(order=order, kind="consume_sale").order_by("created_at", "total_cost"))
    assert len(moves) == 2
    assert {(m.lot_id, m.quantity) for m in moves} == {(lot_a.pk, Decimal("-500")), (lot_b.pk, Decimal("-100"))}
    assert services.order_cost(order) == Decimal("1.30")  # 0.5 kg * 2 + 0.1 kg * 3
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("900") and flour.reserved_qty == 0
    _cache_matches_ledger(wh)


def test_consume_is_idempotent(order_factory, pizza, flour, lots):
    order = order_factory([(pizza, 1, [])])
    services.reserve_for_order(order)
    assert services.consume_order(order) is True
    assert services.consume_order(order) is False
    assert StockMovement.objects.filter(order=order, kind="consume_sale").count() == 1


def test_consume_without_reservation_forces_through(order_factory, pizza, flour, lots):
    order = order_factory([(pizza, 9, [])])  # more than the 1500 g on hand
    assert services.consume_order(order) is True
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("-300")
    assert StockMovement.objects.filter(order=order, lot__isnull=True).exists()
    assert InventoryAlert.objects.filter(kind="negative_stock", stock_item=flour, status="open").exists()
    assert OrderStockReservation.objects.get(order=order).strict is False


def test_release_before_consume(order_factory, pizza, flour, lots, wh):
    order = order_factory([(pizza, 2, [])])
    services.reserve_for_order(order)
    assert services.release_reservation(order) is True
    flour.refresh_from_db()
    assert flour.reserved_qty == 0 and flour.on_hand_qty == Decimal("1500")
    assert OrderStockReservation.objects.get(order=order).status == "released"
    assert services.release_reservation(order) is False
    _cache_matches_ledger(wh)


def test_restore_after_consume_refills_same_lots(order_factory, pizza, flour, lots, wh):
    lot_a, lot_b = lots
    order = order_factory([(pizza, 3, [])])
    services.reserve_for_order(order)
    services.consume_order(order)
    assert services.restore_order(order) is True
    lot_a.refresh_from_db()
    lot_b.refresh_from_db()
    assert lot_a.remaining_qty == Decimal("500") and lot_b.remaining_qty == Decimal("1000")
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("1500")
    assert services.order_cost(order) == 0
    assert OrderStockReservation.objects.get(order=order).status == "restored"
    _cache_matches_ledger(wh)


def test_release_single_order_item(order_factory, pizza, salad, flour, lots, wh):
    order = order_factory([(pizza, 2, []), (pizza, 1, [])])
    services.reserve_for_order(order)
    first = order.items.order_by("created_at").first()
    assert services.release_order_item(first) is True
    flour.refresh_from_db()
    assert flour.reserved_qty == Decimal("200")
    _cache_matches_ledger(wh)


def test_add_items_to_consumed_order_consumes_directly(order_factory, pizza, flour, lots, create_order_item, wh):
    order = order_factory([(pizza, 1, [])])
    services.reserve_for_order(order)
    services.consume_order(order)
    extra = create_order_item(order, menu_item=pizza, item_name="Pizza", quantity=2)
    services.reserve_order_items(order, [extra])
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("900") and flour.reserved_qty == 0
    _cache_matches_ledger(wh)


# ── stock operations ──────────────────────────────────────────────────────


def test_receive_converts_units_and_derives_cost(flour, units, wh):
    lot = services.receive_stock(flour, "2.5", units["kg"], total_cost="10")
    assert lot.received_qty == Decimal("2500")
    assert lot.unit_cost == Decimal("0.004")  # 10 GEL / 2500 g
    assert lot.total_cost == Decimal("10.00")
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("2500")
    assert flour.default_unit_cost == Decimal("0.004")
    _cache_matches_ledger(wh)


def test_receive_rejects_wrong_dimension(flour, units):
    with pytest.raises(UnitMismatch):
        services.receive_stock(flour, "1", units["l"])


def test_waste_specific_lot_and_fifo(flour, lots, units, wh):
    lot_a, lot_b = lots
    entry = services.record_waste(
        WasteEntry(stock_item=flour, lot=lot_b, quantity=Decimal("0.2"), unit=units["kg"], reason="burnt")
    )
    lot_b.refresh_from_db()
    assert lot_b.remaining_qty == Decimal("800")
    assert entry.total_cost == Decimal("0.60")
    services.record_waste(WasteEntry(stock_item=flour, quantity=Decimal("600"), unit=units["g"], reason="spoilage"))
    lot_a.refresh_from_db()
    lot_b.refresh_from_db()
    assert lot_a.remaining_qty == 0 and lot_b.remaining_qty == Decimal("700")
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("700")
    _cache_matches_ledger(wh)


def test_expired_lot_write_off(flour, lots):
    lot_a, _ = lots
    entry = services.record_expired_lot(lot_a)
    lot_a.refresh_from_db()
    assert entry.reason == "expired" and lot_a.remaining_qty == 0 and lot_a.is_expired_out


def test_employee_meal_by_dish_and_raw(flour, lots, pizza, manager, wh, units):
    from apps.staff.models import StaffMember

    member = StaffMember.objects.get(user=manager)
    meal = services.record_employee_meal(EmployeeMeal(staff_member=member, menu_item=pizza, quantity=2))
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("1100")
    assert meal.total_cost == Decimal("0.80")
    services.record_employee_meal(EmployeeMeal(staff_member=member, stock_item=flour, quantity=1, unit=units["kg"]))
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("100")
    _cache_matches_ledger(wh)


def test_employee_meal_dish_without_recipe_is_rejected(salad, manager, wh):
    from apps.inventory.exceptions import InventoryError
    from apps.staff.models import StaffMember

    member = StaffMember.objects.get(user=manager)
    with pytest.raises(InventoryError):
        services.record_employee_meal(EmployeeMeal(staff_member=member, menu_item=salad))


def test_count_creates_synthetic_lot_and_negative_delta_deducts(flour, lots, units, wh):
    adj = services.apply_adjustment(
        StockAdjustment(stock_item=flour, mode="count", quantity=Decimal("2"), unit=units["kg"])
    )
    assert adj.delta_base == Decimal("500")
    assert StockLot.objects.filter(stock_item=flour, reference="adjustment", remaining_qty=500).exists()
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("2000")
    services.apply_adjustment(
        StockAdjustment(stock_item=flour, mode="delta", quantity=Decimal("-300"), unit=units["g"])
    )
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("1700")
    _cache_matches_ledger(wh)


# ── availability & alerts ─────────────────────────────────────────────────


def _platforms(wh):
    RestaurantDeliveryPlatform.objects.create(restaurant=wh, platform="glovo")
    RestaurantDeliveryPlatform.objects.create(restaurant=wh, platform="wolt")
    RestaurantDeliveryPlatform.objects.create(restaurant=wh, platform="bolt_food", is_enabled=False)


def test_dish_auto_disabled_and_reenabled_with_platform_checklists(order_factory, pizza, flour, lots, wh, units):
    _platforms(wh)
    order = order_factory([(pizza, 7, [])])  # 1400 of 1500 g -> 100 left, below one portion
    services.reserve_for_order(order)
    pizza.refresh_from_db()
    assert pizza.auto_disabled_by_stock is True
    assert pizza.is_available is True  # manual flag untouched
    alert = InventoryAlert.objects.get(kind="out_of_stock", menu_item=pizza, status="open")
    tasks = {t.platform.platform: t for t in alert.platform_tasks.select_related("platform")}
    assert set(tasks) == {"glovo", "wolt"}
    assert all(t.action == "disable" and not t.is_done and t.adapter_status == "manual" for t in tasks.values())
    # second recompute does not duplicate
    services.recompute_availability(wh.pk)
    assert InventoryAlert.objects.filter(kind="out_of_stock", menu_item=pizza).count() == 1

    services.receive_stock(flour, "1", units["kg"])
    pizza.refresh_from_db()
    assert pizza.auto_disabled_by_stock is False
    alert.refresh_from_db()
    assert alert.status == "done" and alert.resolved_reason == "auto"
    back = InventoryAlert.objects.get(kind="back_in_stock", menu_item=pizza, status="open")
    assert {t.action for t in back.platform_tasks.all()} == {"enable"}


def test_manual_unavailable_is_never_touched(order_factory, pizza, flour, lots, wh, units):
    pizza.is_available = False
    pizza.save()
    order = order_factory([(pizza, 7, [])])
    services.reserve_for_order(order)
    services.receive_stock(flour, "5", units["kg"])
    pizza.refresh_from_db()
    assert pizza.is_available is False and pizza.auto_disabled_by_stock is False


def test_modifier_auto_disabled(order_factory, pizza, extra_cheese, cheese, flour, lots, units):
    services.receive_stock(cheese, "60", units["g"])
    order = order_factory([(pizza, 1, [extra_cheese])])
    services.reserve_for_order(order)
    extra_cheese.refresh_from_db()
    assert extra_cheese.auto_disabled_by_stock is True
    assert InventoryAlert.objects.filter(kind="out_of_stock", modifier=extra_cheese, status="open").exists()


def test_low_stock_alert_opens_and_resolves(flour, lots, units, wh):
    services.record_waste(WasteEntry(stock_item=flour, quantity=Decimal("1250"), unit=units["g"], reason="spoilage"))
    assert InventoryAlert.objects.filter(kind="low_stock", stock_item=flour, status="open").exists()
    services.receive_stock(flour, "1", units["kg"])
    assert not InventoryAlert.objects.filter(kind="low_stock", stock_item=flour, status="open").exists()


def test_feature_off_clears_flags_and_alerts(order_factory, pizza, flour, lots, wh):
    order = order_factory([(pizza, 7, [])])
    services.reserve_for_order(order)
    services.on_feature_toggled(wh, False)
    pizza.refresh_from_db()
    assert pizza.auto_disabled_by_stock is False
    assert not InventoryAlert.objects.filter(restaurant=wh, status="open").exists()


def test_public_menu_hides_auto_disabled_dish(order_factory, pizza, flour, lots, wh, api_client):
    order = order_factory([(pizza, 7, [])])
    services.reserve_for_order(order)
    resp = api_client.get(f"/api/v1/restaurants/{wh.slug}/menu/")
    names = [i["name"] for cat in resp.json()["data"]["menu"]["categories"] for i in cat["items"]]
    assert "Pizza" not in names
    detail = api_client.get(f"/api/v1/restaurants/{wh.slug}/menu/items/{pizza.pk}/")
    assert detail.status_code == 404


# ── reports ───────────────────────────────────────────────────────────────


def test_buy_list_in_purchase_packs_with_expiring_topup(flour, lots, units):
    # par 2000, available 1500, lot A (500 g) expires before the day after tomorrow
    lines = services.buy_list(flour.restaurant, for_date=timezone.localdate() + timezone.timedelta(days=3))
    assert len(lines) == 1
    line = lines[0]
    assert line.expiring == Decimal("500")
    assert line.purchase_unit.code == "kg"
    assert line.packs == 1 and line.suggested_purchase == Decimal("10")  # 1 kg needed -> one 10 kg bag
    assert "expiring" in line.reasons


def test_low_stock_and_expiring_reports(flour, lots, units):
    assert services.low_stock_report(flour.restaurant) == []
    assert [lot.reference for lot in services.expiring_report(flour.restaurant)] == ["A"]
    services.record_waste(WasteEntry(stock_item=flour, quantity=Decimal("1300"), unit=units["g"], reason="spoilage"))
    assert [i.name for i in services.low_stock_report(flour.restaurant)] == ["Flour"]


def test_recipe_cost_uses_weighted_lot_cost(pizza, flour, lots):
    # 500 g @ 0.002 + 1000 g @ 0.003 -> avg 0.0026667/g; 200 g -> 0.53
    assert services.recipe_cost(pizza) == Decimal("0.53")


def test_rebuild_cache_fixes_drift(flour, lots):
    StockItem.objects.filter(pk=flour.pk).update(on_hand_qty=1)
    drift = services.rebuild_stock_cache(flour.restaurant)
    assert len(drift) == 1
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("1500")
