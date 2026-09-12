"""Splitting items off to a new order and moving an order between tables."""

from decimal import Decimal

import pytest

from apps.orders import services
from apps.orders.models import Order
from apps.orders.services import OrderError
from apps.tables.models import TableSession


@pytest.fixture
def cash_restaurant(restaurant):
    restaurant.cash_enabled = True
    restaurant.save(update_fields=["cash_enabled"])
    return restaurant


@pytest.fixture
def seated_order(create_order, create_order_item, cash_restaurant, table, table_session, menu_item):
    o = create_order(restaurant=cash_restaurant, table=table, table_session=table_session, status="confirmed")
    create_order_item(order=o, menu_item=menu_item, item_name="Khinkali", unit_price=Decimal("10"), quantity=2)
    create_order_item(order=o, item_name="Lemonade", unit_price=Decimal("4"))
    create_order_item(order=o, item_name="Cake", unit_price=Decimal("6"))
    o.calculate_totals()
    return o


@pytest.mark.django_db
class TestSplit:
    def test_split_moves_items_and_recomputes(self, seated_order, user):
        lemonade = seated_order.items.get(item_name="Lemonade")
        cake = seated_order.items.get(item_name="Cake")
        new = services.split_items(seated_order, [lemonade.pk, cake.pk], by=user)
        seated_order.refresh_from_db()
        assert new.pk != seated_order.pk
        assert new.table == seated_order.table and new.table_session == seated_order.table_session
        assert new.status == "confirmed"
        assert set(new.items.values_list("item_name", flat=True)) == {"Lemonade", "Cake"}
        assert new.total == Decimal("10.00")
        assert seated_order.total == Decimal("20.00")
        assert new.status_history.filter(notes__contains="Split from").exists()

    def test_cannot_split_everything_or_paid(self, seated_order, user, cash_restaurant):
        ids = list(seated_order.items.values_list("pk", flat=True))
        with pytest.raises(OrderError) as exc:
            services.split_items(seated_order, ids, by=user)
        assert exc.value.code == "split_all"
        from apps.payments import services as ledger

        ledger.record_payment(
            cash_restaurant, method="card_terminal", amount=Decimal("30"), order=seated_order, by=user
        )
        with pytest.raises(OrderError) as exc:
            services.split_items(seated_order, ids[:1], by=user)
        assert exc.value.code == "already_paid"

    def test_split_api_to_other_table(
        self, authenticated_owner_client, cash_restaurant, seated_order, create_table, table_section
    ):
        other = create_table(restaurant=cash_restaurant, number="T9", section=table_section)
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        cake = seated_order.items.get(item_name="Cake")
        res = c.post(
            f"/api/v1/dashboard/orders/{seated_order.id}/split/",
            {"item_ids": [str(cake.id)], "table_id": str(other.id)},
            format="json",
        )
        assert res.status_code == 201, res.content
        data = res.json().get("data") or res.json()
        new = Order.objects.get(pk=data["new_order"]["id"])
        assert new.table == other
        assert new.table_session.status == "active" and new.table_session.table == other
        other.refresh_from_db()
        assert other.status == "occupied"


@pytest.mark.django_db
class TestMove:
    def test_move_creates_target_session_and_closes_empty_source(
        self, seated_order, user, cash_restaurant, create_table, table_section
    ):
        other = create_table(restaurant=cash_restaurant, number="T7", section=table_section)
        source_session = seated_order.table_session
        source_table = seated_order.table
        source_table.set_occupied()
        services.move_order(seated_order, other, by=user)
        seated_order.refresh_from_db()
        assert seated_order.table == other
        assert seated_order.table_session.table == other
        source_session.refresh_from_db()
        source_table.refresh_from_db()
        assert source_session.status == "closed"
        assert source_table.status == "available"
        assert TableSession.objects.filter(table=other, status="active").count() == 1

    def test_move_keeps_source_open_when_other_orders_remain(
        self, seated_order, user, cash_restaurant, create_table, table_section, create_order, create_order_item
    ):
        session = seated_order.table_session
        sibling = create_order(restaurant=cash_restaurant, table=seated_order.table, table_session=session)
        create_order_item(order=sibling, item_name="Tea", unit_price=Decimal("2"))
        other = create_table(restaurant=cash_restaurant, number="T8", section=table_section)
        services.move_order(seated_order, other, by=user)
        session.refresh_from_db()
        assert session.status == "active"

    def test_move_api(self, authenticated_owner_client, cash_restaurant, seated_order, create_table, table_section):
        other = create_table(restaurant=cash_restaurant, number="T6", section=table_section)
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        res = c.post(f"/api/v1/dashboard/orders/{seated_order.id}/move/", {"table_id": str(other.id)}, format="json")
        assert res.status_code == 200, res.content
        assert (res.json().get("data") or res.json())["table_number"] == "T6"
