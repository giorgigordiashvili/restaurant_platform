"""Table sessions and the ledger: bill, mark-cash-paid, close guard, waiter access."""

from decimal import Decimal

import pytest

from apps.payments import services as ledger


@pytest.fixture
def cash_restaurant(restaurant):
    restaurant.cash_enabled = True
    restaurant.save(update_fields=["cash_enabled"])
    return restaurant


@pytest.fixture
def session_with_orders(cash_restaurant, table_session, create_order, create_order_item):
    o1 = create_order(restaurant=cash_restaurant, table=table_session.table, table_session=table_session)
    create_order_item(order=o1, item_name="x", unit_price=Decimal("10"))
    o1.calculate_totals()
    o2 = create_order(restaurant=cash_restaurant, table=table_session.table, table_session=table_session)
    create_order_item(order=o2, item_name="y", unit_price=Decimal("20"))
    o2.calculate_totals()
    return table_session


@pytest.mark.django_db
class TestSessionPay:
    def test_bill_and_close_guard(self, authenticated_owner_client, cash_restaurant, session_with_orders, user):
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        sid = session_with_orders.id
        res = c.get(f"/api/v1/dashboard/tables/sessions/{sid}/bill/")
        assert res.status_code == 200, res.content
        data = res.json().get("data") or res.json()
        assert data["grand_total"] == "30.00" and data["balance"] == "30.00" and len(data["orders"]) == 2
        res = c.post(f"/api/v1/dashboard/tables/sessions/{sid}/close/", {}, format="json")
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "unpaid_orders"
        assert res.json()["error"]["unpaid_total"] == "30.00"

        ledger.open_shift(cash_restaurant, by=user)
        res = c.post(f"/api/v1/dashboard/tables/sessions/{sid}/mark-cash-paid/", {"tendered": "50"}, format="json")
        assert res.status_code == 201, res.content
        data = res.json().get("data") or res.json()
        assert data["amount"] == "30.00" and data["change"] == "20.00" and len(data["covered_order_numbers"]) == 2

        res = c.get(f"/api/v1/dashboard/tables/sessions/{sid}/bill/")
        data = res.json().get("data") or res.json()
        assert data["balance"] == "0.00" and all(o["is_paid"] for o in data["orders"]) and len(data["payments"]) == 1

        res = c.get("/api/v1/dashboard/tables/sessions/?status=active")
        row = next(r for r in res.json()["results"] if r["id"] == str(sid))
        assert row["orders_summary"]["all_paid"] and row["orders_summary"]["paid_total"] == "30.00"

        res = c.post(f"/api/v1/dashboard/tables/sessions/{sid}/close/", {}, format="json")
        assert res.status_code == 200

    def test_mark_cash_paid_needs_shift(self, authenticated_owner_client, cash_restaurant, session_with_orders):
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        res = c.post(f"/api/v1/dashboard/tables/sessions/{session_with_orders.id}/mark-cash-paid/", {}, format="json")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "shift_required"

    def test_online_payment_counts_as_paid(self, authenticated_owner_client, cash_restaurant, session_with_orders):
        for o in session_with_orders.orders.all():
            ledger.record_payment(
                cash_restaurant,
                method="online_flitt",
                amount=o.total,
                order=o,
                external_id=f"f-{o.pk}",
                allow_overpay=True,
            )
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        res = c.post(f"/api/v1/dashboard/tables/sessions/{session_with_orders.id}/close/", {}, format="json")
        assert res.status_code == 200, res.content

    def test_waiter_can_list_and_close(self, authenticated_waiter_client, waiter_staff, cash_restaurant, table_session):
        c = authenticated_waiter_client
        c.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        assert c.get("/api/v1/dashboard/tables/sessions/").status_code == 200
        res = c.post(f"/api/v1/dashboard/tables/sessions/{table_session.id}/close/", {}, format="json")
        assert res.status_code == 200, res.content
