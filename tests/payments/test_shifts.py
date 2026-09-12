"""Cash shifts: open / close, X and Z reports, movements, and the API around them."""

from decimal import Decimal

import pytest

from apps.payments import services as ledger
from apps.payments.models import CashShift
from apps.payments.services import LedgerError


@pytest.fixture
def cash_restaurant(restaurant):
    restaurant.cash_enabled = True
    restaurant.save(update_fields=["cash_enabled"])
    return restaurant


@pytest.fixture
def order20(create_order, create_order_item, cash_restaurant, table):
    o = create_order(restaurant=cash_restaurant, table=table)
    create_order_item(order=o, item_name="x", unit_price=Decimal("20"))
    o.calculate_totals()
    return o


@pytest.mark.django_db
class TestShiftLifecycle:
    def test_open_close_numbers(self, cash_restaurant, user):
        s1 = ledger.open_shift(cash_restaurant, by=user, opening_float=Decimal("50"))
        assert s1.number == 1 and s1.status == "open"
        with pytest.raises(LedgerError) as exc:
            ledger.open_shift(cash_restaurant, by=user)
        assert exc.value.code == "shift_open"
        ledger.close_shift(s1, by=user, counted_cash=Decimal("50"))
        s2 = ledger.open_shift(cash_restaurant, by=user)
        assert s2.number == 2
        assert ledger.current_shift(cash_restaurant) == s2

    def test_close_freezes_report_and_difference(self, cash_restaurant, user, order20):
        shift = ledger.open_shift(cash_restaurant, by=user, opening_float=Decimal("100"))
        ledger.record_payment(
            cash_restaurant,
            method="cash",
            amount=Decimal("20"),
            tip=Decimal("2"),
            tendered=Decimal("30"),
            order=order20,
            by=user,
        )
        ledger.add_movement(shift, kind="paid_out", amount=Decimal("15"), reason="Milk from the shop", by=user)
        ledger.add_movement(shift, kind="paid_in", amount=Decimal("5"), reason="Float top-up", by=user)
        x = ledger.x_report(shift)
        # 100 + 20 sales + 2 tips - 15 + 5 = 112
        assert x["expected_cash"] == "112.00"
        assert x["by_method"]["cash"]["count"] == 1
        assert x["sales"] == "20.00" and x["tips"] == "2.00"
        closed = ledger.close_shift(shift, by=user, counted_cash=Decimal("110"))
        assert closed.status == "closed"
        assert closed.expected_cash == Decimal("112.00")
        assert closed.difference == Decimal("-2.00")
        assert closed.report["counted_cash"] == "110.00"
        assert closed.report["paid_out"] == "15.00"
        with pytest.raises(LedgerError):
            ledger.close_shift(closed, by=user, counted_cash=Decimal("1"))
        with pytest.raises(LedgerError):
            ledger.add_movement(closed, kind="paid_in", amount=Decimal("1"), reason="late", by=user)

    def test_payment_after_close_needs_new_shift(self, cash_restaurant, user, order20):
        shift = ledger.open_shift(cash_restaurant, by=user)
        ledger.close_shift(shift, by=user, counted_cash=Decimal("0"))
        with pytest.raises(LedgerError) as exc:
            ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("20"), order=order20, by=user)
        assert exc.value.code == "shift_required"

    def test_refunds_and_voids_in_report(self, cash_restaurant, user, order20, create_order_item):
        from apps.orders import services as order_services

        shift = ledger.open_shift(cash_restaurant, by=user)
        extra = create_order_item(order=order20, item_name="y", unit_price=Decimal("7"))
        order20.calculate_totals()
        order_services.void_item(extra, by=user, reason_text="Entered by mistake")
        p = ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("20"), order=order20, by=user)
        ledger.refund_payment(p, amount=Decimal("5"), by=user)
        x = ledger.x_report(shift)
        assert x["voids"]["count"] == 1 and x["voids"]["amount"] == "7.00"
        assert x["refunds"] == "5.00" and x["cash_refunds"] == "5.00"
        assert x["expected_cash"] == "15.00"


@pytest.mark.django_db
class TestShiftApi:
    base = "/api/v1/dashboard/payments/shifts/"

    def test_open_current_close(self, authenticated_owner_client, cash_restaurant):
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        res = c.get(self.base + "current/")
        assert res.status_code == 200 and (res.json().get("data") if "data" in res.json() else None) is None
        res = c.post(self.base + "open/", {"opening_float": "40"}, format="json")
        assert res.status_code == 201, res.content
        shift_id = (res.json().get("data") or res.json())["id"]
        res = c.post(self.base + "open/", {}, format="json")
        assert res.status_code == 409
        res = c.get(self.base + "current/x-report/")
        assert res.status_code == 200
        assert (res.json().get("data") or res.json())["report"]["expected_cash"] == "40.00"
        res = c.post(
            self.base + f"{shift_id}/movements/", {"kind": "paid_out", "amount": "10", "reason": "ice"}, format="json"
        )
        assert res.status_code == 201
        res = c.post(self.base + f"{shift_id}/close/", {"counted_cash": "30"}, format="json")
        assert res.status_code == 200
        data = res.json().get("data") or res.json()
        assert data["status"] == "closed" and data["difference"] == "0.00"
        assert CashShift.objects.get(pk=shift_id).report["paid_out"] == "10.00"

    def test_waiter_opens_but_cannot_close(self, authenticated_waiter_client, waiter_staff, cash_restaurant):
        c = authenticated_waiter_client
        c.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        res = c.post(self.base + "open/", {}, format="json")
        assert res.status_code == 201, res.content
        shift_id = (res.json().get("data") or res.json())["id"]
        res = c.post(self.base + f"{shift_id}/close/", {"counted_cash": "0"}, format="json")
        assert res.status_code == 403

    def test_module_off(self, authenticated_owner_client, restaurant):
        restaurant.cash_enabled = False
        restaurant.save(update_fields=["cash_enabled"])
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        res = authenticated_owner_client.post(self.base + "open/", {}, format="json")
        assert res.status_code == 403
