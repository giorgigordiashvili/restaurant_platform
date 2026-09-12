"""
Kitchen display: role permissions on the order endpoints and the kitchen ticket payload.
"""

from decimal import Decimal

import pytest

from apps.orders.models import Order, OrderItem
from apps.staff.models import StaffMember, StaffRole

pytestmark = pytest.mark.django_db

KITCHEN = "/api/v1/dashboard/orders/kitchen/"
LIST = "/api/v1/dashboard/orders/"
CREATE = "/api/v1/dashboard/orders/create/"
TIPS = "/api/v1/dashboard/orders/tips/report/"


def _tenant(client, restaurant):
    client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
    return client


def _rows(resp):
    """Paginated results, whether or not the {success, data} envelope is applied."""
    body = resp.json()
    return (body.get("data") or body)["results"]


def _status(client, order, status_, **extra):
    return client.patch(f"/api/v1/dashboard/orders/{order.id}/status/", {"status": status_, **extra}, format="json")


@pytest.fixture
def ticket(create_order, create_order_item, restaurant, table, menu_item):
    order = create_order(restaurant=restaurant, table=table, order_type="dine_in", customer_name="Ann")
    order.confirm()
    create_order_item(order, menu_item=menu_item, item_name="Pizza", quantity=2, special_instructions="no onion")
    create_order_item(order, menu_item=menu_item, item_name="Mojito", preparation_station="bar")
    create_order_item(order, menu_item=menu_item, item_name="Gone", status="cancelled")
    return order


class TestKitchenPermissions:
    def test_kitchen_role_can_read_and_move_orders(
        self, authenticated_kitchen_client, kitchen_staff, restaurant, ticket
    ):
        client = _tenant(authenticated_kitchen_client, restaurant)
        assert client.get(KITCHEN).status_code == 200
        assert client.get(LIST).status_code == 200
        for next_status in ("preparing", "ready", "served"):
            resp = _status(client, ticket, next_status)
            assert resp.status_code == 200, resp.content
        ticket.refresh_from_db()
        assert ticket.status == "served"

    def test_kitchen_role_cannot_create_orders_or_see_tips(
        self, authenticated_kitchen_client, kitchen_staff, restaurant, menu_item
    ):
        client = _tenant(authenticated_kitchen_client, restaurant)
        payload = {"order_type": "takeaway", "items": [{"menu_item_id": str(menu_item.id), "quantity": 1}]}
        assert client.post(CREATE, payload, format="json").status_code == 403
        assert client.get(TIPS).status_code == 403

    def test_kitchen_can_refuse_with_reason(self, authenticated_kitchen_client, kitchen_staff, restaurant, ticket):
        client = _tenant(authenticated_kitchen_client, restaurant)
        resp = _status(client, ticket, "cancelled", cancellation_reason="Kitchen: Out of ingredients")
        assert resp.status_code == 200
        ticket.refresh_from_db()
        assert ticket.status == "cancelled"
        assert ticket.cancellation_reason == "Kitchen: Out of ingredients"
        assert ticket.status_history.filter(to_status="cancelled").exists()

    def test_waiter_can_create_and_move(self, authenticated_waiter_client, waiter_staff, restaurant, menu_item):
        client = _tenant(authenticated_waiter_client, restaurant)
        payload = {"order_type": "takeaway", "items": [{"menu_item_id": str(menu_item.id), "quantity": 1}]}
        resp = client.post(CREATE, payload, format="json")
        assert resp.status_code == 201, resp.content
        order = Order.objects.get(order_number=resp.json()["data"]["order_number"])
        assert _status(client, order, "confirmed").status_code == 200
        assert client.get(TIPS).status_code == 403

    def test_warehouse_manager_is_read_only(
        self, create_user, create_staff_member, restaurant, staff_roles, api_client, ticket
    ):
        from rest_framework_simplejwt.tokens import RefreshToken

        user = create_user(email="whm@example.com")
        role = next(r for r in staff_roles if r.name == "warehouse_manager")
        create_staff_member(user=user, restaurant=restaurant, role=role)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
        client = _tenant(api_client, restaurant)
        assert client.get(KITCHEN).status_code == 200
        assert _status(client, ticket, "preparing").status_code == 403

    def test_non_member_is_rejected(self, another_user, api_client, restaurant, ticket):
        from rest_framework_simplejwt.tokens import RefreshToken

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(another_user).access_token}")
        client = _tenant(api_client, restaurant)
        assert client.get(KITCHEN).status_code == 403
        assert _status(client, ticket, "preparing").status_code == 403

    def test_permission_override_opens_access(self, authenticated_waiter_client, waiter_staff, restaurant):
        client = _tenant(authenticated_waiter_client, restaurant)
        assert client.get(TIPS).status_code == 403
        waiter_staff.permissions_override = {"analytics": ["read"]}
        waiter_staff.save()
        assert client.get(TIPS).status_code == 200

    def test_other_tenants_order_is_404(
        self, authenticated_kitchen_client, kitchen_staff, restaurant, another_restaurant, create_order
    ):
        other = create_order(restaurant=another_restaurant, order_type="takeaway")
        client = _tenant(authenticated_kitchen_client, restaurant)
        assert _status(client, other, "preparing").status_code == 404


class TestKitchenPayload:
    def test_ticket_shape(self, authenticated_kitchen_client, kitchen_staff, restaurant, ticket):
        client = _tenant(authenticated_kitchen_client, restaurant)
        rows = _rows(client.get(KITCHEN))
        assert len(rows) == 1
        row = rows[0]
        assert row["order_number"] == ticket.order_number
        assert row["table_number"] == ticket.table.number
        assert row["customer_name"] == "Ann"
        assert row["confirmed_at"] is not None
        assert row["elapsed_minutes"] == 0
        names = {i["item_name"]: i for i in row["items"]}
        assert set(names) == {"Pizza", "Mojito"}  # cancelled item hidden, bar item kept
        assert names["Mojito"]["preparation_station"] == "bar"
        assert names["Pizza"]["special_instructions"] == "no onion"
        assert names["Pizza"]["quantity"] == 2

    def test_statuses_and_filter(self, authenticated_kitchen_client, kitchen_staff, restaurant, create_order):
        for status_ in ("pending", "confirmed", "preparing", "ready", "served", "cancelled"):
            create_order(restaurant=restaurant, order_type="takeaway", status=status_)
        client = _tenant(authenticated_kitchen_client, restaurant)
        seen = {r["status"] for r in _rows(client.get(KITCHEN, {"page_size": 100}))}
        assert seen == {"confirmed", "preparing", "ready"}
        only = {r["status"] for r in _rows(client.get(KITCHEN, {"status": "preparing,ready"}))}
        assert only == {"preparing", "ready"}
        # unknown values fall back to the full kitchen set
        assert len(_rows(client.get(KITCHEN, {"status": "bogus"}))) == 3

    def test_oldest_confirmed_first(self, authenticated_kitchen_client, kitchen_staff, restaurant, create_order):
        from django.utils import timezone

        late = create_order(restaurant=restaurant, order_type="takeaway", status="confirmed")
        early = create_order(restaurant=restaurant, order_type="takeaway", status="confirmed")
        Order.objects.filter(pk=late.pk).update(confirmed_at=timezone.now())
        Order.objects.filter(pk=early.pk).update(confirmed_at=timezone.now() - timezone.timedelta(minutes=20))
        client = _tenant(authenticated_kitchen_client, restaurant)
        rows = _rows(client.get(KITCHEN))
        assert [r["id"] for r in rows] == [str(early.pk), str(late.pk)]
        assert rows[0]["elapsed_minutes"] == 20
