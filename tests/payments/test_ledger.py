"""
The money ledger: allocations, partial / split payments, overpay rules,
idempotent webhooks, receipt numbers under concurrency, refunds, loyalty.
"""

import threading
from decimal import Decimal

from django.db import connection

import pytest

from apps.payments import services as ledger
from apps.payments.models import Payment, PaymentAllocation
from apps.payments.services import LedgerError


@pytest.fixture
def cash_restaurant(restaurant):
    restaurant.cash_enabled = True
    restaurant.save(update_fields=["cash_enabled"])
    return restaurant


@pytest.fixture
def priced_order(create_order, create_order_item, cash_restaurant, table, menu_item):
    order = create_order(restaurant=cash_restaurant, table=table, order_type="dine_in")
    create_order_item(order=order, menu_item=menu_item, item_name="A", unit_price=Decimal("10.00"), quantity=2)
    create_order_item(order=order, item_name="B", unit_price=Decimal("5.50"), quantity=1)
    order.calculate_totals()
    order.refresh_from_db()
    assert order.total == Decimal("25.50")
    return order


@pytest.fixture
def open_shift(cash_restaurant, user):
    return ledger.open_shift(cash_restaurant, by=user, opening_float=Decimal("100"))


@pytest.mark.django_db
class TestRecordPayment:
    def test_full_cash_payment_allocates_and_numbers(self, cash_restaurant, priced_order, open_shift, user):
        p = ledger.record_payment(
            cash_restaurant, method="cash", amount=Decimal("25.50"), tendered=Decimal("30"), order=priced_order, by=user
        )
        assert p.status == "completed"
        assert p.change_given == Decimal("4.50")
        assert p.shift == open_shift
        assert p.restaurant == cash_restaurant
        assert p.receipt_number.startswith("RCP-")
        assert PaymentAllocation.objects.get(payment=p).amount == Decimal("25.50")
        assert ledger.is_paid(priced_order)
        assert ledger.balance(priced_order) == 0

    def test_cash_requires_open_shift_when_module_on(self, cash_restaurant, priced_order, user):
        with pytest.raises(LedgerError) as exc:
            ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("25.50"), order=priced_order, by=user)
        assert exc.value.code == "shift_required"

    def test_cash_without_shift_when_module_off(self, restaurant, priced_order, user):
        restaurant.cash_enabled = False
        restaurant.save(update_fields=["cash_enabled"])
        p = ledger.record_payment(restaurant, method="cash", amount=Decimal("25.50"), order=priced_order, by=user)
        assert p.shift is None and p.status == "completed"

    def test_card_terminal_without_shift(self, cash_restaurant, priced_order, user):
        p = ledger.record_payment(
            cash_restaurant, method="card_terminal", amount=Decimal("25.50"), order=priced_order, by=user
        )
        assert p.status == "completed" and p.shift is None

    def test_partial_then_remainder(self, cash_restaurant, priced_order, open_shift, user):
        ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("10"), order=priced_order, by=user)
        assert ledger.balance(priced_order) == Decimal("15.50")
        assert not ledger.is_paid(priced_order)
        ledger.record_payment(
            cash_restaurant, method="card_terminal", amount=Decimal("15.50"), order=priced_order, by=user
        )
        assert ledger.is_paid(priced_order)
        assert ledger.paid_amount(priced_order) == Decimal("25.50")

    def test_overpay_refused_for_staff(self, cash_restaurant, priced_order, open_shift, user):
        with pytest.raises(LedgerError) as exc:
            ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("30"), order=priced_order, by=user)
        assert exc.value.code == "overpay"
        ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("25.50"), order=priced_order, by=user)
        with pytest.raises(LedgerError) as exc:
            ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("1"), order=priced_order, by=user)
        assert exc.value.code == "already_paid"

    def test_overpay_allowed_for_webhooks_and_idempotent(self, cash_restaurant, priced_order):
        p1 = ledger.record_payment(
            cash_restaurant,
            method="online_bog",
            amount=Decimal("30"),
            order=priced_order,
            external_id="bog-1",
            allow_overpay=True,
        )
        p2 = ledger.record_payment(
            cash_restaurant,
            method="online_bog",
            amount=Decimal("30"),
            order=priced_order,
            external_id="bog-1",
            allow_overpay=True,
        )
        assert p1.pk == p2.pk
        assert Payment.objects.filter(external_payment_id="bog-1").count() == 1
        assert ledger.paid_amount(priced_order) == Decimal("30")

    def test_insufficient_tendered(self, cash_restaurant, priced_order, open_shift, user):
        with pytest.raises(LedgerError) as exc:
            ledger.record_payment(
                cash_restaurant,
                method="cash",
                amount=Decimal("25.50"),
                tendered=Decimal("20"),
                order=priced_order,
                by=user,
            )
        assert exc.value.code == "insufficient_tendered"

    def test_session_payment_covers_unpaid_orders_oldest_first(
        self, cash_restaurant, open_shift, user, table_session, create_order, create_order_item
    ):
        o1 = create_order(restaurant=cash_restaurant, table=table_session.table, table_session=table_session)
        create_order_item(order=o1, item_name="x", unit_price=Decimal("10"))
        o1.calculate_totals()
        o2 = create_order(restaurant=cash_restaurant, table=table_session.table, table_session=table_session)
        create_order_item(order=o2, item_name="y", unit_price=Decimal("20"))
        o2.calculate_totals()
        assert ledger.session_balance(table_session) == Decimal("30")
        p = ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("15"), session=table_session, by=user)
        allocs = {a.order_id: a.amount for a in p.allocations.all()}
        assert allocs == {o1.pk: Decimal("10"), o2.pk: Decimal("5")}
        assert [o.pk for o in ledger.unpaid_orders(table_session)] == [o2.pk]
        assert ledger.session_balance(table_session) == Decimal("15")
        ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("15"), session=table_session, by=user)
        assert ledger.unpaid_orders(table_session) == []
        with pytest.raises(LedgerError) as exc:
            ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("1"), session=table_session, by=user)
        assert exc.value.code == "nothing_to_pay"

    def test_cancelled_orders_are_never_due(self, cash_restaurant, priced_order, user):
        priced_order.status = "cancelled"
        priced_order.save(update_fields=["status"])
        assert ledger.is_paid(priced_order)
        with pytest.raises(LedgerError):
            ledger.record_payment(
                cash_restaurant, method="card_terminal", amount=Decimal("1"), order=priced_order, by=user
            )

    def test_loyalty_accrued_once(self, cash_restaurant, priced_order, open_shift, user, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "apps.loyalty.services.accrue_platform_points", lambda o, source: calls.append((o.pk, source))
        )
        ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("10"), order=priced_order, by=user)
        assert calls == []  # not fully paid yet
        ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("15.50"), order=priced_order, by=user)
        assert calls == [(priced_order.pk, "cash")]


@pytest.mark.django_db
class TestSplitEvenly:
    def test_exact_sums(self):
        assert ledger.split_evenly(Decimal("10"), 3) == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]
        assert sum(ledger.split_evenly(Decimal("99.99"), 7)) == Decimal("99.99")
        assert ledger.split_evenly(Decimal("0"), 2) == [Decimal("0"), Decimal("0")]

    def test_invalid(self):
        with pytest.raises(LedgerError):
            ledger.split_evenly(Decimal("10"), 0)


@pytest.mark.django_db(transaction=True)
class TestReceiptNumbers:
    def test_unique_under_threads(self, restaurant):
        numbers = []
        errors = []
        barrier = threading.Barrier(6)

        def worker():
            try:
                barrier.wait(timeout=5)
                numbers.append(ledger.next_receipt_number(restaurant.pk))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(set(numbers)) == 6
        assert sorted(int(n.rsplit("-", 1)[1]) for n in numbers) == [1, 2, 3, 4, 5, 6]


@pytest.mark.django_db
class TestRefunds:
    def test_refund_reduces_paid_and_flags_payment(self, cash_restaurant, priced_order, open_shift, user):
        p = ledger.record_payment(cash_restaurant, method="cash", amount=Decimal("25.50"), order=priced_order, by=user)
        r = ledger.refund_payment(p, amount=Decimal("5.50"), by=user, reason_details="Wrong dish")
        assert r.status == "completed" and r.method == "cash" and r.shift == open_shift and r.order == priced_order
        p.refresh_from_db()
        assert p.status == "partially_refunded"
        assert ledger.paid_amount(priced_order) == Decimal("20")
        with pytest.raises(LedgerError) as exc:
            ledger.refund_payment(p, amount=Decimal("100"), by=user)
        assert exc.value.code == "exceeds_refundable"
        ledger.refund_payment(p, amount=Decimal("20"), by=user)
        p.refresh_from_db()
        assert p.status == "refunded"


@pytest.mark.django_db
class TestRecordPaymentApi:
    def test_record_cash_via_api(self, authenticated_owner_client, cash_restaurant, priced_order, open_shift):
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        res = authenticated_owner_client.post(
            "/api/v1/dashboard/payments/record/",
            {"method": "cash", "amount": "25.50", "tendered": "50", "order_id": str(priced_order.id)},
            format="json",
        )
        assert res.status_code == 201, res.content
        body = res.json()
        data = body.get("data") or body
        assert data["change"] == "24.50"
        assert data["balance"] == "0.00"
        assert data["paid_order_numbers"] == [priced_order.order_number]

    def test_overpay_409(self, authenticated_owner_client, cash_restaurant, priced_order, open_shift):
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        res = authenticated_owner_client.post(
            "/api/v1/dashboard/payments/record/",
            {"method": "card_terminal", "amount": "99", "order_id": str(priced_order.id)},
            format="json",
        )
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "overpay"

    def test_module_off_403(self, authenticated_owner_client, restaurant, priced_order):
        restaurant.cash_enabled = False
        restaurant.save(update_fields=["cash_enabled"])
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        res = authenticated_owner_client.post(
            "/api/v1/dashboard/payments/record/",
            {"method": "card_terminal", "amount": "1", "order_id": str(priced_order.id)},
            format="json",
        )
        assert res.status_code == 403

    def test_waiter_can_take_payment_but_not_refund(
        self, authenticated_waiter_client, waiter_staff, cash_restaurant, priced_order, open_shift
    ):
        authenticated_waiter_client.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        res = authenticated_waiter_client.post(
            "/api/v1/dashboard/payments/record/",
            {"method": "cash", "amount": "25.50", "order_id": str(priced_order.id)},
            format="json",
        )
        assert res.status_code == 201, res.content
        payment_id = (res.json().get("data") or res.json())["payment"]["id"]
        res = authenticated_waiter_client.post(
            f"/api/v1/dashboard/payments/{payment_id}/refund/", {"amount": "1"}, format="json"
        )
        assert res.status_code == 403

    def test_split_even_preview(self, authenticated_owner_client, cash_restaurant, priced_order):
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        res = authenticated_owner_client.post(
            "/api/v1/dashboard/payments/split-even/", {"order_id": str(priced_order.id), "ways": 4}, format="json"
        )
        assert res.status_code == 200
        data = res.json().get("data") or res.json()
        assert data["shares"] == ["6.38", "6.38", "6.37", "6.37"]

    def test_reasons_defaults(self, authenticated_owner_client, cash_restaurant):
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        res = authenticated_owner_client.get("/api/v1/dashboard/payments/reasons/?kind=void")
        assert res.status_code == 200
        data = res.json().get("data") or res.json()
        assert data[0]["id"] is None and data[0]["kind"] == "void"
