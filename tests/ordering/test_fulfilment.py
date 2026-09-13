"""validate_fulfilment + the customer order endpoint + the BOG / Flitt initiate path (shared builder)."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from rest_framework.test import APIClient

import pytest

from apps.ordering import services
from apps.orders.models import Order
from tests.ordering.conftest import FAR, NEAR

TBI = ZoneInfo("Asia/Tbilisi")
URL = "/api/v1/orders/create/"


def now_open():
    return datetime(2026, 9, 14, 12, 0, tzinfo=TBI)


def now_closed():
    return datetime(2026, 9, 14, 8, 0, tzinfo=TBI)


@pytest.mark.django_db
class TestValidate:
    def test_takeaway_asap_when_open(self, ordering):
        f = services.validate_fulfilment(ordering, "takeaway", subtotal=Decimal("20"), now=now_open())
        assert f.scheduled_for is None and f.fee == 0 and f.ready_at == now_open().replace(minute=20)

    def test_closed_carries_next_opening(self, ordering):
        with pytest.raises(services.FulfilmentError) as exc:
            services.validate_fulfilment(ordering, "takeaway", subtotal=Decimal("20"), now=now_closed())
        assert exc.value.code == "closed" and exc.value.extra["next_opening"].startswith("2026-09-14T10:00")

    def test_cutoff_before_close(self, ordering):
        late = datetime(2026, 9, 14, 21, 45, tzinfo=TBI)
        with pytest.raises(services.FulfilmentError) as exc:
            services.validate_fulfilment(ordering, "takeaway", subtotal=Decimal("20"), now=late)
        assert exc.value.code == "closing_soon"

    def test_scheduled_slot_valid_and_invalid(self, ordering):
        slot = datetime(2026, 9, 14, 13, 30, tzinfo=TBI)
        f = services.validate_fulfilment(
            ordering, "takeaway", subtotal=Decimal("20"), scheduled_for=slot, now=now_open()
        )
        assert f.scheduled_for == slot and f.ready_at == slot
        with pytest.raises(services.FulfilmentError) as exc:
            services.validate_fulfilment(
                ordering, "takeaway", subtotal=Decimal("20"), scheduled_for=slot.replace(minute=33), now=now_open()
            )
        assert exc.value.code == "slot_invalid"
        # scheduling works even while closed right now
        f = services.validate_fulfilment(
            ordering, "takeaway", subtotal=Decimal("20"), scheduled_for=slot, now=now_closed()
        )
        assert f.scheduled_for == slot

    def test_delivery_zone_fee_and_minimum(self, ordering, zone):
        f = services.validate_fulfilment(
            ordering,
            "delivery",
            subtotal=Decimal("20"),
            lat=NEAR[0],
            lng=NEAR[1],
            address="Rustaveli 12",
            now=now_open(),
        )
        assert f.fee == Decimal("5.00") and f.zone == zone and f.ready_at == now_open().replace(minute=30)
        with pytest.raises(services.FulfilmentError) as exc:
            services.validate_fulfilment(
                ordering, "delivery", subtotal=Decimal("10"), lat=NEAR[0], lng=NEAR[1], address="x", now=now_open()
            )
        assert exc.value.code == "min_order" and exc.value.extra["missing"] == "5.00"
        with pytest.raises(services.FulfilmentError) as exc:
            services.validate_fulfilment(
                ordering, "delivery", subtotal=Decimal("20"), lat=FAR[0], lng=FAR[1], address="x", now=now_open()
            )
        assert exc.value.code == "out_of_zone"
        with pytest.raises(services.FulfilmentError) as exc:
            services.validate_fulfilment(
                ordering, "delivery", subtotal=Decimal("20"), lat=NEAR[0], lng=NEAR[1], address="", now=now_open()
            )
        assert exc.value.code == "address_required"

    def test_paused_and_disabled(self, ordering, zone):
        services.pause(ordering, 15, "Busy")
        with pytest.raises(services.FulfilmentError) as exc:
            services.validate_fulfilment(ordering, "takeaway", subtotal=Decimal("20"))
        assert exc.value.code == "paused" and exc.value.extra["resume_at"]
        services.resume(ordering)
        cfg = services.settings_for(ordering)
        cfg.pickup_enabled = False
        cfg.delivery_enabled = False
        cfg.save()
        with pytest.raises(services.FulfilmentError) as exc:
            services.validate_fulfilment(ordering, "takeaway", subtotal=Decimal("20"), now=now_open())
        assert exc.value.code == "pickup_disabled"
        with pytest.raises(services.FulfilmentError) as exc:
            services.validate_fulfilment(ordering, "delivery", subtotal=Decimal("20"), now=now_open())
        assert exc.value.code == "delivery_disabled"

    def test_module_off_keeps_legacy_takeaway(self, restaurant):
        f = services.validate_fulfilment(restaurant, "takeaway", subtotal=Decimal("1"), now=now_closed())
        assert f.fee == 0
        with pytest.raises(services.FulfilmentError):
            services.validate_fulfilment(restaurant, "delivery", subtotal=Decimal("1"), now=now_open())


@pytest.mark.django_db
class TestCustomerOrderCreate:
    def _post(self, ordering, menu_item, **extra):
        data = {
            "restaurant_slug": ordering.slug,
            "order_type": "takeaway",
            "customer_name": "Nino",
            "customer_phone": "+995555123456",
            "items": [{"menu_item_id": str(menu_item.id), "quantity": 2}],
            **extra,
        }
        return APIClient().post(URL, data, format="json")

    def test_takeaway_open(self, ordering, menu_item, monkeypatch):
        monkeypatch.setattr("apps.ordering.services.timezone.now", now_open)
        res = self._post(ordering, menu_item)
        assert res.status_code == 201, res.content
        order = Order.objects.get(order_number=res.data["data"]["order_number"])
        assert order.order_type == "takeaway" and order.estimated_ready_at is not None and order.delivery_fee == 0

    def test_takeaway_closed_returns_code(self, ordering, menu_item, monkeypatch):
        monkeypatch.setattr("apps.ordering.services.timezone.now", now_closed)
        res = self._post(ordering, menu_item)
        assert res.status_code == 400 and res.data["error"]["code"] == "closed"
        assert res.data["error"]["next_opening"]
        assert not Order.objects.filter(restaurant=ordering).exists()

    def test_delivery_with_fee_in_total(self, ordering, menu_item, zone, monkeypatch):
        monkeypatch.setattr("apps.ordering.services.timezone.now", now_open)
        res = self._post(
            ordering,
            menu_item,
            order_type="delivery",
            delivery_address="Rustaveli 12",
            lat=NEAR[0],
            lng=NEAR[1],
            address={"street": "Rustaveli", "building": "12"},
            delivery_instructions="Ring twice",
            scheduled_for="2026-09-14T14:00:00+04:00",
        )
        assert res.status_code == 201, res.content
        order = Order.objects.get(order_number=res.data["data"]["order_number"])
        assert order.delivery_fee == Decimal("5.00") and order.total == Decimal("25.00")
        assert order.delivery_zone == zone and order.address_json["building"] == "12"
        assert order.scheduled_for.astimezone(TBI).hour == 14 and order.delivery_instructions == "Ring twice"
        body = APIClient().get(f"/api/v1/orders/{order.order_number}/").json()
        data = body.get("data") or body
        assert data["delivery_fee"] == "5.00" and data["scheduled_for"] and data["delivery"] is None

    def test_delivery_out_of_zone(self, ordering, menu_item, zone, monkeypatch):
        monkeypatch.setattr("apps.ordering.services.timezone.now", now_open)
        res = self._post(ordering, menu_item, order_type="delivery", delivery_address="x", lat=FAR[0], lng=FAR[1])
        assert res.status_code == 400 and res.data["error"]["code"] == "out_of_zone"

    def test_dine_in_untouched(self, ordering, menu_item, table, monkeypatch):
        monkeypatch.setattr("apps.ordering.services.timezone.now", now_closed)
        res = self._post(ordering, menu_item, order_type="dine_in", table_id=str(table.id))
        assert res.status_code == 201, res.content


class FakeBog:
    def __init__(self):
        self.calls = []

    def create_order(self, payload, idempotency_key=None):
        self.calls.append(payload)
        return {"id": "bog-1", "_links": {"redirect": {"href": "https://pay.example/1"}}}


@pytest.mark.django_db
class TestInitiateShared:
    def test_bog_initiate_uses_shared_builder_with_fee_line(self, ordering, menu_item, zone, monkeypatch, settings):
        settings.BOG_CLIENT_ID = "x"
        settings.BOG_CLIENT_SECRET = "y"
        settings.BOG_WEBHOOK_URL = "https://api.example/webhook"
        fake = FakeBog()
        monkeypatch.setattr("apps.payments.bog.views.get_client", lambda: fake)
        monkeypatch.setattr("apps.ordering.services.timezone.now", now_open)
        res = APIClient().post(
            "/api/v1/payments/bog/initiate/",
            {
                "target": "order",
                "return_url": "https://aimenu.ge/payments/return",
                "order_payload": {
                    "restaurant_slug": ordering.slug,
                    "order_type": "delivery",
                    "customer_name": "Nino",
                    "customer_phone": "+995555123456",
                    "delivery_address": "Rustaveli 12",
                    "lat": NEAR[0],
                    "lng": NEAR[1],
                    "items": [{"menu_item_id": str(menu_item.id), "quantity": 2}],
                },
            },
            format="json",
        )
        assert res.status_code == 201, res.content
        order = Order.objects.get(order_number=res.data["data"]["order_number"])
        assert order.status == "pending_payment" and order.total == Decimal("25.00")
        basket = fake.calls[0]["purchase_units"]["basket"]
        assert basket[-1]["product_id"] == "delivery_fee" and basket[-1]["total_price"] == 5.0
        assert fake.calls[0]["purchase_units"]["total_amount"] == 25.0

    def test_bog_initiate_rejects_closed(self, ordering, menu_item, monkeypatch, settings):
        settings.BOG_CLIENT_ID = "x"
        settings.BOG_CLIENT_SECRET = "y"
        monkeypatch.setattr("apps.payments.bog.views.get_client", lambda: FakeBog())
        monkeypatch.setattr("apps.ordering.services.timezone.now", now_closed)
        res = APIClient().post(
            "/api/v1/payments/bog/initiate/",
            {
                "target": "order",
                "return_url": "https://aimenu.ge/payments/return",
                "order_payload": {
                    "restaurant_slug": ordering.slug,
                    "order_type": "takeaway",
                    "items": [{"menu_item_id": str(menu_item.id), "quantity": 1}],
                },
            },
            format="json",
        )
        assert res.status_code == 400 and "closed" in res.data["error"]["message"].lower()
        assert not Order.objects.filter(restaurant=ordering).exists()
