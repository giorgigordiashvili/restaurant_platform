"""Corrected totals, order/item discounts, comps, voids (service + API)."""

from decimal import Decimal

import pytest

from apps.orders import services
from apps.orders.models import OrderDiscount
from apps.orders.services import OrderError
from apps.payments.models import DiscountReason


@pytest.fixture
def cash_restaurant(restaurant):
    restaurant.cash_enabled = True
    restaurant.tax_rate = Decimal("10")
    restaurant.service_charge = Decimal("5")
    restaurant.save(update_fields=["cash_enabled", "tax_rate", "service_charge"])
    return restaurant


@pytest.fixture
def order(create_order, create_order_item, cash_restaurant, table, menu_item):
    o = create_order(restaurant=cash_restaurant, table=table)
    create_order_item(order=o, menu_item=menu_item, item_name="Khinkali", unit_price=Decimal("10"), quantity=3)
    create_order_item(order=o, item_name="Lemonade", unit_price=Decimal("4"), quantity=1)
    o.calculate_totals()
    o.refresh_from_db()
    return o


@pytest.mark.django_db
class TestCalculateTotals:
    def test_tax_and_service_on_net(self, order):
        # 34 gross, 10% tax + 5% service on net
        assert order.subtotal == Decimal("34.00")
        assert order.tax_amount == Decimal("3.40")
        assert order.service_charge == Decimal("1.70")
        assert order.total == Decimal("39.10")

    def test_cancelled_items_excluded(self, order, user):
        item = order.items.get(item_name="Lemonade")
        services.void_item(item, by=user, reason_text="Changed mind")
        order.refresh_from_db()
        assert order.subtotal == Decimal("30.00")
        assert order.tax_amount == Decimal("3.00")
        assert order.total == Decimal("34.50")

    def test_percent_and_fixed_discounts(self, order, user):
        services.apply_discount(order, mode="percent", value=10, by=user, reason_text="Regular")
        order.refresh_from_db()
        assert order.discount_amount == Decimal("3.40")
        # net 30.60 -> tax 3.06, service 1.53
        assert order.total == Decimal("35.19")
        services.apply_discount(order, mode="fixed", value=100, by=user, reason_text="Big")
        order.refresh_from_db()
        assert order.discount_amount == Decimal("34.00")  # capped at what was left
        assert order.total == Decimal("0.00")

    def test_item_discount_and_comp(self, order, user):
        khinkali = order.items.get(item_name="Khinkali")
        services.discount_item(khinkali, mode="percent", value=50, by=user, reason_text="Late")
        khinkali.refresh_from_db()
        order.refresh_from_db()
        assert khinkali.discount_amount == Decimal("15.00") and khinkali.net_price == Decimal("15.00")
        assert order.discount_amount == Decimal("15.00")
        lemonade = order.items.get(item_name="Lemonade")
        services.comp_item(lemonade, by=user, reason_text="Owner's treat")
        lemonade.refresh_from_db()
        order.refresh_from_db()
        assert lemonade.is_comped and lemonade.discount_amount == Decimal("4.00")
        assert order.discount_amount == Decimal("19.00")
        assert order.subtotal == Decimal("34.00")
        # net 15 -> tax 1.50, service 0.75
        assert order.total == Decimal("17.25")

    def test_loyalty_tier_row_replaced(self, order):
        class Tier:
            discount_percent = 5
            name = "Silver"

        services.apply_loyalty_tier_discount(order, Tier())
        services.apply_loyalty_tier_discount(order, Tier())
        assert OrderDiscount.objects.filter(order=order, kind="loyalty_tier").count() == 1
        order.refresh_from_db()
        assert order.discount_amount == Decimal("1.70")
        services.apply_loyalty_tier_discount(order, None)
        order.refresh_from_db()
        assert order.discount_amount == Decimal("0.00")


@pytest.mark.django_db
class TestRules:
    def test_reason_required(self, order, user):
        with pytest.raises(OrderError) as exc:
            services.apply_discount(order, mode="percent", value=10, by=user)
        assert exc.value.code == "reason_required"

    def test_manager_reason(self, order, user, cash_restaurant):
        reason = DiscountReason.objects.create(
            restaurant=cash_restaurant, kind="discount", label="VIP", requires_manager=True
        )
        with pytest.raises(OrderError) as exc:
            services.apply_discount(order, mode="percent", value=10, by=user, reason=reason, manager=False)
        assert exc.value.code == "manager_required"
        services.apply_discount(order, mode="percent", value=10, by=user, reason=reason, manager=True)

    def test_refused_after_payment(self, order, user, cash_restaurant):
        from apps.payments import services as ledger

        ledger.record_payment(cash_restaurant, method="card_terminal", amount=Decimal("39.10"), order=order, by=user)
        with pytest.raises(OrderError) as exc:
            services.apply_discount(order, mode="percent", value=10, by=user, reason_text="x")
        assert exc.value.code == "already_paid"

    def test_void_last_item_cancels_order(self, order, user):
        for item in list(order.items.all()):
            services.void_item(item, by=user, reason_text="Mistake")
        order.refresh_from_db()
        assert order.status == "cancelled"
        assert "voided" in order.cancellation_reason

    def test_void_records_who_why_and_kitchen_flag(self, order, user):
        services.transition_order(order, "confirmed", by=user)
        item = order.items.get(item_name="Lemonade")
        item.refresh_from_db()
        assert item.was_sent_to_kitchen
        services.void_item(item, by=user, reason_text="Spilled")
        item.refresh_from_db()
        assert item.status == "cancelled" and item.voided_by == user and item.void_reason_text == "Spilled"
        assert item.voided_at is not None

    def test_void_restores_stock(self, order, user, monkeypatch):
        called = []
        monkeypatch.setattr(
            "apps.inventory.hooks.on_order_item_cancelled", lambda item, by=None: called.append(item.pk)
        )
        item = order.items.get(item_name="Lemonade")
        services.void_item(item, by=user, reason_text="x")
        assert called == [item.pk]


@pytest.mark.django_db
class TestApi:
    def test_discount_void_comp_endpoints(self, authenticated_owner_client, cash_restaurant, order):
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        base = f"/api/v1/dashboard/orders/{order.id}/"
        res = c.post(base + "discount/", {"mode": "percent", "value": "10", "reason_text": "Regular"}, format="json")
        assert res.status_code == 200, res.content
        data = res.json().get("data") or res.json()
        assert data["discount_amount"] == "3.40" and len(data["discounts"]) == 1
        res = c.delete(base + "discount/", {}, format="json")
        assert res.status_code == 200
        assert (res.json().get("data") or res.json())["discount_amount"] == "0.00"

        lemonade = order.items.get(item_name="Lemonade")
        res = c.post(base + f"items/{lemonade.id}/comp/", {"reason_text": "Treat"}, format="json")
        assert res.status_code == 200
        items = {i["item_name"]: i for i in (res.json().get("data") or res.json())["items"]}
        assert items["Lemonade"]["is_comped"] and items["Lemonade"]["net_price"] == "0.00"

        khinkali = order.items.get(item_name="Khinkali")
        res = c.post(base + f"items/{khinkali.id}/void/", {}, format="json")
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "reason_required"
        res = c.post(base + f"items/{khinkali.id}/void/", {"reason_text": "Mistake"}, format="json")
        assert res.status_code == 200
        items = {i["item_name"]: i for i in (res.json().get("data") or res.json())["items"]}
        assert items["Khinkali"]["status"] == "cancelled" and items["Khinkali"]["void_reason_label"] == "Mistake"

    def test_waiter_cannot_discount_but_can_void(
        self, authenticated_waiter_client, waiter_staff, cash_restaurant, order
    ):
        c = authenticated_waiter_client
        c.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        base = f"/api/v1/dashboard/orders/{order.id}/"
        res = c.post(base + "discount/", {"mode": "percent", "value": "10", "reason_text": "x"}, format="json")
        assert res.status_code == 403
        item = order.items.get(item_name="Lemonade")
        res = c.post(base + f"items/{item.id}/void/", {"reason_text": "Guest changed mind"}, format="json")
        assert res.status_code == 200, res.content

    def test_item_status_cancel_requires_reason(self, authenticated_owner_client, cash_restaurant, order):
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = cash_restaurant.slug
        item = order.items.get(item_name="Lemonade")
        url = f"/api/v1/dashboard/orders/{order.id}/items/{item.id}/status/"
        res = c.patch(url, {"status": "cancelled"}, format="json")
        assert res.status_code == 400
        res = c.patch(url, {"status": "cancelled", "reason_text": "Out of lemons"}, format="json")
        assert res.status_code == 200
        item.refresh_from_db()
        assert item.void_reason_text == "Out of lemons"
