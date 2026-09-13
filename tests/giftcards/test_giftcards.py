"""Gift cards: issue / sell / redeem / refund-to-card / void / expire, checkout redemption, online purchase, API, admin, report."""

from datetime import timedelta
from decimal import Decimal

from django.test import Client
from django.utils import timezone

from rest_framework.test import APIClient

import pytest

from apps.giftcards import services
from apps.giftcards.models import GiftCard
from apps.notifications.models import OutboundMessage
from apps.orders.models import Order, OrderItem
from apps.payments import services as ledger
from apps.payments.models import Payment

D = "/api/v1/dashboard/gift-cards/"


@pytest.fixture(autouse=True)
def _env(settings):
    settings.MAIN_DOMAIN = "localhost"
    settings.SMS_PROVIDER = "console"


@pytest.fixture
def gifts(restaurant):
    restaurant.gift_cards_enabled = True
    restaurant.cash_enabled = True
    restaurant.accepts_remote_orders = True
    restaurant.save()
    from apps.notifications import services as notifications

    cfg = notifications.settings_for(restaurant)
    cfg.guest_sms = True
    cfg.save()
    return restaurant


@pytest.fixture
def shift(gifts, user):
    return ledger.open_shift(gifts, by=user, opening_float=Decimal("20"))


@pytest.fixture
def owner_api(authenticated_owner_client, restaurant):
    authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
    return authenticated_owner_client


@pytest.fixture
def owner_admin(user, restaurant, staff_roles, create_staff_member):
    create_staff_member(user=user, restaurant=restaurant, role=next(r for r in staff_roles if r.name == "owner"))
    client = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
    client.force_login(user)
    return client


def bill(restaurant, menu_item, total="30.00"):
    o = Order.objects.create(restaurant=restaurant, order_type="dine_in", status="confirmed", source="pos")
    OrderItem.objects.create(
        order=o,
        menu_item=menu_item,
        item_name="Dish",
        unit_price=Decimal(total),
        quantity=1,
        total_price=Decimal(total),
    )
    o.calculate_totals()
    return o


@pytest.mark.django_db
class TestLifecycle:
    def test_sell_and_redeem(self, gifts, shift, user, menu_item):
        card = services.sell_pos(
            gifts,
            "50",
            method="cash",
            by=user,
            tendered="50",
            recipient={"name": "Ana", "phone": "+995555000001"},
            kind="digital",
        )
        assert card.code.startswith("GC-") and card.balance == Decimal("50.00") and card.sold_payment.order_id is None
        assert card.sold_payment.payment_method == "cash" and card.sold_payment.shift_id == shift.pk
        assert OutboundMessage.objects.filter(kind="gift_card", body__contains=card.code).exists()
        order = bill(gifts, menu_item)
        payment = services.redeem(card, "20", order=order, by=user)
        card.refresh_from_db()
        assert (
            payment.payment_method == "gift_card"
            and card.balance == Decimal("30.00")
            and ledger.balance(order) == Decimal("10.00")
        )
        with pytest.raises(services.GiftCardError) as exc:
            services.redeem(card, "31", order=bill(gifts, menu_item, "40"), by=user)
        assert exc.value.code == "insufficient_balance"
        services.redeem(card, "30", order=bill(gifts, menu_item, "30"), by=user)
        card.refresh_from_db()
        assert card.status == "used_up"
        services.refund_to_card(card, "5", by=user)
        card.refresh_from_db()
        assert card.status == "active" and card.balance == Decimal("5.00")
        assert card.transactions.count() == 4
        assert services.lookup(gifts, card.code.lower().replace("-", "")).pk == card.pk

    def test_void_expire_and_report(self, gifts, user):
        card = services.issue(gifts, "100", by=user)
        services.void(card, by=user)
        card.refresh_from_db()
        assert card.status == "void" and card.balance == 0
        with pytest.raises(services.GiftCardError):
            services.check_usable(card)
        old = services.issue(gifts, "10", by=user, expires_at=timezone.now() - timedelta(days=1))
        assert services.expire() == 1
        old.refresh_from_db()
        assert old.status == "expired"
        rep = services.report(gifts, timezone.localdate(), timezone.localdate())
        assert rep["sold_count"] == 1 and rep["sold_value"] == Decimal("10.00") and rep["outstanding"] == 0

    def test_order_cancel_refunds_card(self, gifts, shift, user, menu_item):
        card = services.issue(gifts, "40", by=user)
        order = bill(gifts, menu_item)
        services.redeem(card, "30", order=order, by=user)
        from apps.orders.services import transition_order

        transition_order(order, "cancelled", by=user, cancellation_reason="test")
        card.refresh_from_db()
        assert card.balance == Decimal("40.00")
        assert Payment.objects.get(order=order, payment_method="gift_card").refunds.count() == 1


@pytest.mark.django_db
class TestCheckout:
    def test_cash_checkout_applies_card(self, gifts, user, menu_item, table):
        card = services.issue(gifts, "15", by=user)
        res = APIClient().post(
            "/api/v1/orders/create/",
            {
                "restaurant_slug": gifts.slug,
                "order_type": "dine_in",
                "table_id": str(table.id),
                "gift_card_code": card.code,
                "items": [{"menu_item_id": str(menu_item.id), "quantity": 1}],
            },
            format="json",
        )
        assert res.status_code == 201, res.content
        order = Order.objects.get(order_number=res.data["data"]["order_number"])
        assert order.gift_card_id == card.pk and order.gift_card_applied == Decimal("10.00")
        card.refresh_from_db()
        assert card.balance == Decimal("5.00") and ledger.is_paid(order)

    def test_bad_code_rejected(self, gifts, user, menu_item, table):
        res = APIClient().post(
            "/api/v1/orders/create/",
            {
                "restaurant_slug": gifts.slug,
                "order_type": "dine_in",
                "table_id": str(table.id),
                "gift_card_code": "GC-XXXX-XXXX",
                "items": [{"menu_item_id": str(menu_item.id), "quantity": 1}],
            },
            format="json",
        )
        assert res.status_code == 400 and "gift_card_code" in res.json()

    def test_bog_online_purchase_then_activation(self, gifts, monkeypatch, settings):
        settings.BOG_CLIENT_ID = "x"
        settings.BOG_CLIENT_SECRET = "y"

        class FakeBog:
            def create_order(self, payload, idempotency_key=None):
                self.payload = payload
                return {"id": "bog-gc", "_links": {"redirect": {"href": "https://pay.example/gc"}}}

        fake = FakeBog()
        monkeypatch.setattr("apps.payments.bog.views.get_client", lambda: fake)
        res = APIClient().post(
            "/api/v1/payments/bog/initiate/",
            {
                "target": "gift_card",
                "return_url": "https://aimenu.ge/gift-cards/return",
                "gift_card_payload": {
                    "restaurant_slug": gifts.slug,
                    "amount": "75",
                    "recipient_name": "Beka",
                    "recipient_phone": "+995555000002",
                    "message": "Happy birthday",
                },
            },
            format="json",
        )
        assert res.status_code == 201, res.content
        card = GiftCard.objects.get(pk=res.data["data"]["gift_card_id"])
        assert card.status == "pending" and card.balance == 0 and fake.payload["purchase_units"]["total_amount"] == 75.0
        from apps.payments.bog.views import _apply_receipt
        from apps.payments.models import BogTransaction

        txn = BogTransaction.objects.get(bog_order_id="bog-gc")
        _apply_receipt(txn, {"order_status": {"key": "completed"}}, source="poll")
        card.refresh_from_db()
        assert (
            card.status == "active"
            and card.balance == Decimal("75.00")
            and card.sold_payment.payment_method == "online_bog"
        )
        assert OutboundMessage.objects.filter(kind="gift_card", to="+995555000002").exists()
        _apply_receipt(txn, {"order_status": {"key": "completed"}}, source="poll")
        assert Payment.objects.filter(gift_card=card).count() == 1


@pytest.mark.django_db
class TestApiAndAdmin:
    def test_dashboard_flow(self, gifts, shift, owner_api, menu_item):
        res = owner_api.post(
            D + "sell/", {"amount": "50", "method": "cash", "tendered": "50", "recipient_name": "Ana"}, format="json"
        )
        assert res.status_code == 201, res.content
        code = res.data["code"]
        look = owner_api.get(D + f"lookup/?code={code}")
        assert look.status_code == 200 and look.data["balance"] == "50.00" and look.data["is_usable"] is True
        assert owner_api.get(D + "lookup/?code=GC-NOPE-NOPE").status_code == 404
        order = bill(gifts, menu_item)
        res = owner_api.post(D + "redeem/", {"code": code, "amount": "30", "order_id": str(order.pk)}, format="json")
        assert (
            res.status_code == 200
            and res.data["balance"] == "0.00"
            and res.data["card_balance"] == "20.00"
            and res.data["paid_order_numbers"] == [order.order_number]
        )
        assert owner_api.get(D + "summary/").data["active"] == 1
        cid = (
            res.data["payment"]["gift_card"]
            if "gift_card" in res.data["payment"]
            else GiftCard.objects.get(code=code).pk
        )
        detail = owner_api.get(D + f"{cid}/")
        assert detail.status_code == 200 and len(detail.data["transactions"]) == 2
        assert (
            owner_api.post(D + f"{cid}/adjust/", {"amount": "5", "note": "goodwill"}, format="json").data["balance"]
            == "25.00"
        )
        assert owner_api.post(D + f"{cid}/void/", {}, format="json").data["status"] == "void"
        assert len(owner_api.get(D + "?status=void").data) == 1

    def test_public_balance_and_card_page(self, gifts, user):
        card = services.issue(gifts, "20", by=user, recipient={"name": "Ana"})
        res = APIClient().get(f"/api/v1/gift-cards/{gifts.slug}/balance/?code={card.code}")
        assert res.status_code == 200 and res.json()["data"]["balance"] == "20.00"
        assert APIClient().get(f"/api/v1/gift-cards/{gifts.slug}/balance/?code=GC-0000-0000").status_code == 404
        html = APIClient().get(f"/api/v1/gift-cards/card/{card.token}/", HTTP_ACCEPT="text/html").content.decode()
        assert card.code in html
        data = APIClient().get(f"/api/v1/gift-cards/card/{card.token}/?format=json").json()["data"]
        assert data["balance"] == "20.00"

    def test_module_off(self, restaurant, owner_api):
        assert owner_api.get(D + "summary/").status_code == 403

    def test_admin_batch_and_list(self, gifts, owner_admin):
        res = owner_admin.post(
            "/tenant-admin/giftcards/giftcardbatchpage/create/",
            {"count": "3", "amount": "25", "design": "classic", "note": "promo"},
        )
        assert res.status_code == 302
        assert GiftCard.objects.filter(restaurant=gifts, initial_value=25).count() == 3
        html = owner_admin.get("/tenant-admin/giftcards/giftcardbatchpage/").content.decode()
        assert 'data-testid="giftcard-batch-codes"' in html
        html = owner_admin.get("/tenant-admin/giftcards/giftcard/").content.decode()
        assert "GC-" in html and 'data-testid="giftcards-summary"' in html
        card = GiftCard.objects.filter(restaurant=gifts).first()
        assert owner_admin.get(f"/tenant-admin/giftcards/giftcard/{card.pk}/void/").status_code == 302
        card.refresh_from_db()
        assert card.status == "void"
