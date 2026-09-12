"""End-to-end through the order APIs: create reserves, accept consumes, cancel releases."""

from decimal import Decimal

import pytest

from apps.inventory.models import OrderStockReservation, StockMovement
from apps.orders.models import Order

pytestmark = pytest.mark.django_db

POS_CREATE = "/api/v1/dashboard/orders/create/"
CUSTOMER_CREATE = "/api/v1/orders/create/"


def _pos_payload(menu_item, qty, table=None, modifiers=()):
    payload = {"order_type": "takeaway", "items": [{"menu_item_id": str(menu_item.id), "quantity": qty}]}
    if modifiers:
        payload["items"][0]["modifier_ids"] = [str(m.id) for m in modifiers]
    if table is not None:
        payload["table_id"] = str(table.id)
    return payload


def test_pos_create_reserves(manager_api, pizza, flour, lots):
    resp = manager_api.post(POS_CREATE, _pos_payload(pizza, 2), format="json")
    assert resp.status_code == 201, resp.content
    flour.refresh_from_db()
    assert flour.reserved_qty == Decimal("400")
    order = Order.objects.get(order_number=resp.json()["data"]["order_number"])
    assert order.stock_reservation.status == "reserved"


def test_pos_create_short_stock_is_409_and_rolls_back(manager_api, pizza, flour, lots):
    resp = manager_api.post(POS_CREATE, _pos_payload(pizza, 8), format="json")
    assert resp.status_code == 409, resp.content
    body = resp.json()
    # Test settings use DRF's stock handler (raw detail); production wraps it
    # in the {success, error: {code, details}} envelope.
    items = body.get("items") or body["error"]["details"]["items"]
    assert items[0]["name"] == "Pizza"
    assert "Not enough stock" in (body.get("message") or body["error"]["message"])
    assert Order.objects.count() == 0
    flour.refresh_from_db()
    assert flour.reserved_qty == 0


def test_customer_create_reserves_and_rejects_when_short(api_client, pizza, flour, lots, wh):
    payload = {
        "restaurant_slug": wh.slug,
        "order_type": "takeaway",
        "customer_name": "Ann",
        "items": [{"menu_item_id": str(pizza.id), "quantity": 7}],
    }
    assert api_client.post(CUSTOMER_CREATE, payload, format="json").status_code == 201
    flour.refresh_from_db()
    assert flour.reserved_qty == Decimal("1400")
    # 100 g left: the dish is sold out now, so it is rejected as unavailable.
    resp = api_client.post(CUSTOMER_CREATE, payload, format="json")
    assert resp.status_code == 400
    assert Order.objects.count() == 1


def _status(client, order, status_, **extra):
    return client.patch(f"/api/v1/dashboard/orders/{order.id}/status/", {"status": status_, **extra}, format="json")


@pytest.mark.parametrize("accept_status", ["confirmed", "preparing"])
def test_accepting_consumes(manager_api, pizza, flour, lots, accept_status):
    resp = manager_api.post(POS_CREATE, _pos_payload(pizza, 2), format="json")
    order = Order.objects.get(order_number=resp.json()["data"]["order_number"])
    assert _status(manager_api, order, accept_status).status_code == 200
    flour.refresh_from_db()
    assert flour.reserved_qty == 0 and flour.on_hand_qty == Decimal("1100")
    assert order.stock_reservation.status == "consumed"
    # later moves do nothing more
    assert _status(manager_api, order, "ready").status_code == 200
    assert StockMovement.objects.filter(order=order, kind="consume_sale").count() == 1


def test_cancel_before_and_after_accept(manager_api, pizza, flour, lots):
    a = Order.objects.get(
        order_number=manager_api.post(POS_CREATE, _pos_payload(pizza, 1), format="json").json()["data"]["order_number"]
    )
    b = Order.objects.get(
        order_number=manager_api.post(POS_CREATE, _pos_payload(pizza, 1), format="json").json()["data"]["order_number"]
    )
    _status(manager_api, b, "confirmed")
    assert _status(manager_api, a, "cancelled", cancellation_reason="changed mind").status_code == 200
    assert _status(manager_api, b, "cancelled", cancellation_reason="kitchen closed").status_code == 200
    flour.refresh_from_db()
    assert flour.reserved_qty == 0 and flour.on_hand_qty == Decimal("1500")
    assert OrderStockReservation.objects.get(order=a).status == "released"
    assert OrderStockReservation.objects.get(order=b).status == "restored"


def test_add_item_to_pending_order_reserves_and_can_refuse(manager_api, pizza, flour, lots):
    order = Order.objects.get(
        order_number=manager_api.post(POS_CREATE, _pos_payload(pizza, 1), format="json").json()["data"]["order_number"]
    )
    url = f"/api/v1/dashboard/orders/{order.id}/items/"
    assert manager_api.post(url, {"menu_item_id": str(pizza.id), "quantity": 2}, format="json").status_code == 200
    flour.refresh_from_db()
    assert flour.reserved_qty == Decimal("600")
    resp = manager_api.post(url, {"menu_item_id": str(pizza.id), "quantity": 5}, format="json")
    assert resp.status_code == 409
    assert order.items.count() == 2


def test_item_cancel_releases_its_share(manager_api, pizza, flour, lots):
    order = Order.objects.get(
        order_number=manager_api.post(POS_CREATE, _pos_payload(pizza, 3), format="json").json()["data"]["order_number"]
    )
    item = order.items.get()
    resp = manager_api.patch(
        f"/api/v1/dashboard/orders/{order.id}/items/{item.id}/status/", {"status": "cancelled"}, format="json"
    )
    assert resp.status_code == 200
    flour.refresh_from_db()
    assert flour.reserved_qty == 0


def test_feature_off_leaves_no_trace(manager_api, pizza, flour, lots, wh):
    wh.warehouse_enabled = False
    wh.save()
    resp = manager_api.post(POS_CREATE, _pos_payload(pizza, 50), format="json")
    assert resp.status_code == 201
    assert not OrderStockReservation.objects.exists()
    flour.refresh_from_db()
    assert flour.reserved_qty == 0


def test_transition_order_helper_is_used_by_payment_flows(pizza, flour, lots, order_factory):
    """Payment webhooks call transition_order(pending_payment -> pending); the hold must survive."""
    from apps.inventory import services
    from apps.orders.services import transition_order

    order = order_factory([(pizza, 2, [])], status="pending_payment")
    services.reserve_for_order(order)
    transition_order(order, "pending", notes="paid")
    assert order.status_history.filter(to_status="pending").exists()
    flour.refresh_from_db()
    assert flour.reserved_qty == Decimal("400")
    # a rejected payment releases
    other = order_factory([(pizza, 1, [])], status="pending_payment")
    services.reserve_for_order(other)
    transition_order(other, "cancelled", cancellation_reason="rejected")
    flour.refresh_from_db()
    assert flour.reserved_qty == Decimal("400")


def test_paid_order_rereserves_after_ttl_release(pizza, flour, lots, order_factory):
    from apps.inventory import services
    from apps.orders.services import transition_order

    order = order_factory([(pizza, 2, [])], status="pending_payment")
    services.reserve_for_order(order)
    services.release_reservation(order)
    # Someone else takes almost everything meanwhile.
    other = order_factory([(pizza, 7, [])])
    services.reserve_for_order(other)
    transition_order(order, "pending", notes="paid late")  # never refused
    flour.refresh_from_db()
    assert flour.reserved_qty == Decimal("1800")
