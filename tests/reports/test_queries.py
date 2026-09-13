"""Report queries against a small, known set of orders / payments / stock movements."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.orders.models import Order
from apps.payments import services as ledger
from apps.reports import queries
from apps.reports.periods import Period

TZ = ZoneInfo("Asia/Tbilisi")
DAY = date(2026, 9, 10)


def _at(d, hour, minute=0):
    return datetime.combine(d, datetime.min.time(), tzinfo=TZ).replace(hour=hour, minute=minute)


@pytest.fixture
def period():
    return Period("custom", DAY, DAY + timedelta(days=1), TZ)


@pytest.fixture
def data(restaurant, user, menu_item, create_order, create_order_item, waiter_user):
    """
    Day 1: a 30 ₾ dine-in order (2 x khinkali 10 + lemonade 10... names differ),
    a cancelled order, a takeaway with a 10 % discount + a voided item; a
    cash and a card payment. Day 2 at 01:30 local (still day 2 in Tbilisi,
    day 1 in UTC): one more order. Day 0: an order outside the period.
    """
    restaurant.cash_enabled = False
    restaurant.save(update_fields=["cash_enabled"])
    made = {}
    seq = iter(range(1, 100))

    def mk(status, when, items, **kwargs):
        # Explicit numbers: the daily counter would collide once created_at is moved.
        o = create_order(restaurant=restaurant, status=status, order_number=f"T-{next(seq):03d}", **kwargs)
        for name, price, qty in items:
            create_order_item(
                order=o,
                menu_item=menu_item if name == "Khinkali" else None,
                item_name=name,
                unit_price=Decimal(price),
                quantity=qty,
            )
        o.calculate_totals()
        Order.objects.filter(pk=o.pk).update(created_at=when)
        o.refresh_from_db()
        return o

    made["dine"] = mk(
        "completed",
        _at(DAY, 13),
        [("Khinkali", "10", 2), ("Lemonade", "10", 1)],
        order_type="dine_in",
        server=waiter_user,
        tip_amount=Decimal("3"),
    )
    made["cancelled"] = mk("cancelled", _at(DAY, 14), [("Khinkali", "10", 1)])
    made["takeaway"] = mk(
        "served", _at(DAY, 20), [("Khinkali", "10", 1), ("Cake", "6", 1)], order_type="takeaway", handled_by=user
    )
    from apps.orders import services as order_services

    order_services.apply_discount(made["takeaway"], mode="percent", value=10, by=user, reason_text="Regular")
    cake = made["takeaway"].items.get(item_name="Cake")
    order_services.void_item(cake, by=user, reason_text="Mistake")
    made["takeaway"].refresh_from_db()
    # The discount / void happened "now"; pin them into the period like the orders.
    from apps.orders.models import OrderDiscount, OrderItem

    OrderItem.objects.filter(pk=cake.pk).update(voided_at=_at(DAY, 20, 5))
    OrderDiscount.objects.filter(order=made["takeaway"]).update(created_at=_at(DAY, 20, 1))
    made["late"] = mk("confirmed", _at(DAY + timedelta(days=1), 1, 30), [("Khinkali", "10", 1)])
    made["outside"] = mk("completed", _at(DAY - timedelta(days=1), 12), [("Khinkali", "10", 5)])

    p1 = ledger.record_payment(
        restaurant, method="cash", amount=Decimal("30"), tip=Decimal("3"), order=made["dine"], by=user
    )
    p2 = ledger.record_payment(restaurant, method="card_terminal", amount=Decimal("9"), order=made["takeaway"], by=user)
    from apps.payments.models import Payment

    Payment.objects.filter(pk__in=[p1.pk, p2.pk]).update(completed_at=_at(DAY, 15))
    return made


@pytest.mark.django_db
class TestSales:
    def test_summary_counts_accepted_orders_in_local_time(self, restaurant, period, data):
        s = queries.sales_summary(restaurant, period)
        # dine 30 + takeaway 9 (10 - 10%) + late 10 = 49 net; the cancelled one and day-0 are out
        assert s["orders"] == 3
        assert s["net_sales"] == Decimal("49.00")
        assert s["gross_total"] == Decimal("52.00")  # + 3 tip on the dine-in
        assert s["discounts"] == Decimal("1.00")
        assert s["tips"] == Decimal("3.00")
        assert s["cancellations"] == {"count": 1, "value": Decimal("10.00")}
        assert s["voids"]["count"] == 1 and s["voids"]["value"] == Decimal("6.00")
        assert s["avg_ticket"] == Decimal("17.33")

    def test_by_day_is_zero_filled_and_tz_aware(self, restaurant, period, data):
        rows = queries.sales_by_day(restaurant, period)
        assert [r["day"] for r in rows] == [DAY, DAY + timedelta(days=1)]
        assert rows[0]["orders"] == 2 and rows[0]["net"] == Decimal("39.00")
        assert rows[1]["orders"] == 1 and rows[1]["net"] == Decimal("10.00")  # 01:30 local lands on day 2

    def test_by_hour_and_type(self, restaurant, period, data):
        hours = {r["hour"]: r["orders"] for r in queries.sales_by_hour(restaurant, period)}
        assert hours[13] == 1 and hours[20] == 1 and hours[1] == 1 and len(hours) == 24
        types = {r["order_type"]: r for r in queries.sales_by_type(restaurant, period)}
        assert types["takeaway"]["net"] == Decimal("9.00") and types["dine_in"]["orders"] == 2

    def test_by_method_reconciles(self, restaurant, period, data):
        rows = {r["method"]: r for r in queries.sales_by_method(restaurant, period)}
        assert rows["cash"]["amount"] == Decimal("30.00") and rows["cash"]["tips"] == Decimal("3.00")
        assert rows["card_terminal"]["amount"] == Decimal("9.00")
        assert rows["unrecorded"]["amount"] == Decimal("13.00")  # 52 gross - 39 recorded

    def test_compare_period(self, restaurant, period, data):
        c = queries.compare_period(restaurant, period)
        assert c["previous"]["net_sales"] == Decimal("50.00")  # the day-0 order
        assert c["delta"]["net_sales"] == Decimal("-2.0")


@pytest.mark.django_db
class TestMenu:
    def test_items_and_categories(self, restaurant, period, data, menu_category):
        items = {r["name"]: r for r in queries.items_report(restaurant, period)}
        # Item revenue is net of item-level discounts only (the takeaway's 10 % is order-level).
        assert items["Khinkali"]["qty"] == 4 and items["Khinkali"]["revenue"] == Decimal("40.00")
        assert items["Khinkali"]["category"] == "Appetizers"
        assert items["Lemonade"]["category"] == "—"
        assert "Cake" not in items  # voided
        cats = {r["category"]: r for r in queries.categories_report(restaurant, period)}
        assert cats["Appetizers"]["qty"] == 4
        assert sum(r["share"] for r in cats.values()) == Decimal("100.0")

    def test_menu_engineering_marks_untracked(self, restaurant, period, data):
        eng = queries.menu_engineering(restaurant, period)
        assert eng["rows"] == []
        assert {r["name"] for r in eng["untracked"]} == {"Khinkali", "Lemonade"}


@pytest.mark.django_db
class TestStaffAndShifts:
    def test_staff_report(self, restaurant, period, data, waiter_user, user):
        r = queries.staff_report(restaurant, period)
        servers = {s["user_id"]: s for s in r["servers"]}
        assert servers[str(waiter_user.pk)]["tips"] == Decimal("3.00") and servers[str(waiter_user.pk)]["orders"] == 1
        handlers = {h["user_id"]: h for h in r["handlers"]}
        assert handlers[str(user.pk)]["discounts"] == 1 and handlers[str(user.pk)]["voids"] == 1

    def test_shifts_report(self, restaurant, period, user):
        restaurant.cash_enabled = True
        restaurant.save(update_fields=["cash_enabled"])
        shift = ledger.open_shift(restaurant, by=user, opening_float=Decimal("10"))
        ledger.close_shift(shift, by=user, counted_cash=Decimal("12"))
        from apps.payments.models import CashShift

        CashShift.objects.filter(pk=shift.pk).update(opened_at=_at(DAY, 9))
        rows = queries.shifts_report(restaurant, period)
        assert len(rows) == 1 and rows[0]["difference"] == Decimal("2.00")


@pytest.mark.django_db
class TestReservationsReviews:
    def test_reservations(self, restaurant, period, user):
        from apps.reservations.models import Reservation

        for status, size in (("completed", 4), ("no_show", 2), ("cancelled", 3), ("confirmed", 2)):
            Reservation.objects.create(
                restaurant=restaurant,
                customer=user,
                guest_name="G",
                guest_phone="1",
                reservation_date=DAY,
                reservation_time="19:00",
                party_size=size,
                status=status,
            )
        r = queries.reservations_report(restaurant, period)
        assert r["total"] == 4 and r["covers"] == 6 and r["no_show"] == 1
        assert r["no_show_rate"] == Decimal("33.3")
        assert r["by_day"][0]["reservations"] == 4 and r["by_day"][1]["reservations"] == 0

    def test_reviews(self, restaurant, period, user, create_order):
        from apps.reviews.models import Review

        for rating in (5, 4, 1):
            o = create_order(restaurant=restaurant, status="completed", customer=user)
            rv = Review.objects.create(restaurant=restaurant, order=o, user=user, rating=rating)
            Review.objects.filter(pk=rv.pk).update(created_at=_at(DAY, 12))
        r = queries.reviews_report(restaurant, period)
        assert r["count"] == 3 and r["average"] == Decimal("3.33")
        assert r["distribution"][5] == 1 and r["distribution"][1] == 1


@pytest.mark.django_db
def test_online_orders_section(restaurant, period, data, menu_item):
    from decimal import Decimal as D

    from apps.ordering.models import Delivery
    from apps.orders.models import Order

    o = Order.objects.filter(restaurant=restaurant, order_type="takeaway").first()
    assert o is not None
    o.source = "web"
    o.delivery_fee = D("0")
    o.packaging_fee = D("1.00")
    o.save()
    o.calculate_totals()
    d = Order.objects.create(
        restaurant=restaurant,
        order_type="delivery",
        status="completed",
        source="web",
        subtotal=D("30"),
        total=D("36"),
        delivery_fee=D("6"),
        created_at=o.created_at,
        completed_at=o.created_at,
    )
    Order.objects.filter(pk=d.pk).update(created_at=o.created_at)
    Delivery.objects.create(
        restaurant=restaurant,
        order=d,
        provider="wolt_drive",
        status="delivered",
        cost=D("4.50"),
        delivered_at=o.created_at,
    )
    rows = {r["order_type"]: r for r in queries.online_orders(restaurant, period)}
    assert rows["takeaway"]["packaging"] == D("1.00") and rows["takeaway"]["orders"] == 1
    assert rows["delivery"]["delivery_fees"] == D("6.00") and rows["delivery"]["courier_cost"] == D("4.50")
