"""Two customers race for the last portions: exactly one of them gets it."""

import threading
from decimal import Decimal

from django.db import connection, transaction

import pytest

from apps.inventory import services
from apps.inventory.exceptions import InsufficientStock
from apps.inventory.models import OrderStockReservation, StockItem, UnitOfMeasure
from apps.orders.models import Order, OrderItem


@pytest.mark.django_db(transaction=True)
def test_only_one_of_two_concurrent_orders_wins(create_user, create_restaurant, create_menu_item):
    owner = create_user(email="race-owner@example.com")
    restaurant = create_restaurant(owner=owner, name="Race", slug="race", warehouse_enabled=True)
    # Transaction tests flush the DB; re-seed the unit when an earlier one ran first.
    g, _ = UnitOfMeasure.objects.get_or_create(
        code="g", defaults={"name": "gram", "dimension": "mass", "factor_to_base": 1}
    )
    flour = StockItem.objects.create(restaurant=restaurant, name="Flour", base_unit=g)
    services.receive_stock(flour, "300", g)
    pizza = create_menu_item(restaurant, name="Pizza", price=Decimal("10"))
    from apps.inventory.models import RecipeLine

    RecipeLine.objects.create(menu_item=pizza, stock_item=flour, quantity=Decimal("200"), unit=g)

    orders = []
    for _ in range(2):
        order = Order.objects.create(restaurant=restaurant, order_type="takeaway")
        OrderItem.objects.create(
            order=order, menu_item=pizza, item_name="Pizza", unit_price=10, quantity=1, total_price=10
        )
        orders.append(order)

    results = {}
    barrier = threading.Barrier(2)

    def worker(idx, order):
        try:
            barrier.wait(timeout=5)
            with transaction.atomic():
                services.reserve_for_order(order)
            results[idx] = "ok"
        except InsufficientStock:
            results[idx] = "short"
        except Exception as exc:  # pragma: no cover - surfaces unexpected failures
            results[idx] = repr(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i, o)) for i, o in enumerate(orders)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert sorted(results.values()) == ["ok", "short"], results
    flour.refresh_from_db()
    assert flour.reserved_qty == Decimal("200")
    assert OrderStockReservation.objects.count() == 1
