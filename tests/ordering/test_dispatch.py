"""Courier orchestration: own riders, auto-request, Wolt Drive / Glovo ODR requests and webhooks, cancel, refresh, guest messages."""

import hashlib
import hmac
import json
from decimal import Decimal

from rest_framework.test import APIClient

import jwt
import pytest

from apps.notifications.models import Notification, OutboundMessage
from apps.ordering import dispatch, services
from apps.ordering.models import Delivery, DeliveryEvent
from apps.orders.services import transition_order
from tests.ordering.conftest import make_delivery_order

WD_WEBHOOK = "/api/v1/delivery/wolt-drive/webhook/"
GLOVO_CB = "/api/v1/delivery/glovo-odr/callback/"


@pytest.mark.django_db
class TestOwnCourier:
    def test_assign_pickup_deliver_completes_order(
        self, delivery_order, courier, user, django_capture_on_commit_callbacks
    ):
        d = dispatch.request_courier(delivery_order, by=user)
        assert d.status == "requested" and d.provider == "own"
        dispatch.assign_own(d, courier, by=user)
        assert d.status == "assigned" and d.courier_name == "Gio"
        with django_capture_on_commit_callbacks(execute=True):
            dispatch.courier_update(d, "picked_up", by=user, lat=41.71, lng=44.82)
        assert d.picked_up_at and d.courier_lat == Decimal("41.710000")
        assert OutboundMessage.objects.filter(kind="order_on_the_way", to="+995555123456").exists()
        dispatch.courier_update(d, "delivered", by=user)
        delivery_order.refresh_from_db()
        assert d.status == "delivered" and delivery_order.status == "completed"
        with pytest.raises(dispatch.DispatchError):
            dispatch.courier_update(d, "picked_up", by=user)

    def test_request_refuses_non_delivery_and_no_pin(self, ordering, menu_item, zone, user):
        o = make_delivery_order(ordering, menu_item)
        o.order_type = "takeaway"
        o.save()
        with pytest.raises(dispatch.DispatchError) as exc:
            dispatch.request_courier(o, by=user)
        assert exc.value.code == "not_delivery"
        o2 = make_delivery_order(ordering, menu_item)
        o2.delivery_lat = None
        o2.save()
        with pytest.raises(dispatch.DispatchError) as exc:
            dispatch.request_courier(o2, by=user)
        assert exc.value.code == "no_coordinates"

    def test_auto_request_on_ready_and_cancel_propagates(
        self, delivery_order, user, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            transition_order(delivery_order, "confirmed", by=user)
        assert OutboundMessage.objects.filter(kind="order_accepted").exists()
        assert dispatch.delivery_for(delivery_order, create=False) is None  # auto-request is on 'ready'
        transition_order(delivery_order, "ready", by=user)
        d = dispatch.delivery_for(delivery_order, create=False)
        assert d is not None and d.status == "requested"
        transition_order(delivery_order, "cancelled", by=user, cancellation_reason="guest")
        d.refresh_from_db()
        assert d.status == "cancelled"

    def test_takeaway_ready_message(self, ordering, menu_item, user, django_capture_on_commit_callbacks):
        o = make_delivery_order(ordering, menu_item)
        o.order_type = "takeaway"
        o.save()
        with django_capture_on_commit_callbacks(execute=True):
            transition_order(o, "ready", by=user)
        assert OutboundMessage.objects.filter(kind="order_ready").exists()


WD_PROMISE = (
    200,
    {
        "id": "promise-1",
        "price": {"amount": 650, "currency": "GEL"},
        "pickup": {"eta": "2026-09-14T12:40:00Z"},
        "dropoff": {"eta": "2026-09-14T13:05:00Z"},
        "valid_until": "2026-09-14T12:35:00Z",
    },
)
WD_CREATED = (
    200,
    {
        "id": "wd-1",
        "status": "INFO_RECEIVED",
        "wolt_order_reference_id": "WOR-77",
        "tracking": {"url": "https://track.wolt.com/x"},
        "price": {"amount": 650, "currency": "GEL"},
        "pickup": {"eta": "2026-09-14T12:40:00Z"},
        "dropoff": {"eta": "2026-09-14T13:05:00Z"},
    },
)


@pytest.mark.django_db
class TestWoltDrive:
    def _request(self, order, fake_courier, user):
        fake_courier.queue.extend([WD_PROMISE, WD_CREATED])
        return dispatch.request_courier(order, provider="wolt_drive", by=user, sync=True)

    def test_quote_then_create(self, delivery_order, courier_link, fake_courier, user):
        d = self._request(delivery_order, fake_courier, user)
        assert d.status == "requested" and d.external_id == "WOR-77" and d.cost == Decimal("6.50")
        assert d.tracking_url.startswith("https://track") and d.quote["promise_id"] == "promise-1"
        promise, create = fake_courier.calls
        assert promise["url"].endswith("/v1/venues/venue-9/shipment-promises")
        assert promise["headers"]["Authorization"] == "Bearer wd-key"
        body = create["json"]
        assert (
            body["shipment_promise_id"] == "promise-1"
            and body["merchant_order_reference_id"] == delivery_order.order_number
        )
        assert len(body["order_number"]) <= 5 and body["dropoff"]["location"]["coordinates"]["lat"] == 41.72
        assert body["recipient"]["phone_number"] == "+995555123456" and body["parcels"][0]["count"] == 2
        assert body["dropoff"]["address_details"]["apartment"] == "7"

    def test_webhook_moves_status_and_dedupes(self, delivery_order, courier_link, fake_courier, user):
        d = self._request(delivery_order, fake_courier, user)
        claims = {
            "id": "evt-1",
            "type": "order.picked_up",
            "details": {"wolt_order_reference_id": "WOR-77", "courier": {"name": "Beka", "phone_number": "+995500"}},
        }
        token = jwt.encode(claims, "wd-secret", algorithm="HS256")
        res = APIClient().generic("POST", WD_WEBHOOK, json.dumps({"token": token}), content_type="application/json")
        assert res.status_code == 200, res.content
        d.refresh_from_db()
        assert d.status == "picked_up" and d.courier_name == "Beka"
        assert OutboundMessage.objects.filter(kind="order_on_the_way", body__contains="track.wolt.com").exists()
        res = APIClient().generic("POST", WD_WEBHOOK, json.dumps({"token": token}), content_type="application/json")
        assert res.json()["status"] == "already_processed"
        bad = jwt.encode(claims, "wrong", algorithm="HS256")
        assert APIClient().generic("POST", WD_WEBHOOK, bad, content_type="text/plain").status_code == 401
        delivered = jwt.encode(
            {"id": "evt-2", "type": "order.delivered", "details": {"wolt_order_reference_id": "WOR-77"}},
            "wd-secret",
            algorithm="HS256",
        )
        APIClient().generic("POST", WD_WEBHOOK, delivered, content_type="text/plain")
        delivery_order.refresh_from_db()
        assert delivery_order.status == "completed" and DeliveryEvent.objects.filter(delivery=d).count() == 2

    def test_rejected_marks_failed_and_notifies(
        self, delivery_order, courier_link, fake_courier, user, django_capture_on_commit_callbacks
    ):
        fake_courier.queue.extend([WD_PROMISE, (400, {"error": "out of range"})])
        d = dispatch.request_courier(delivery_order, provider="wolt_drive", by=user, sync=True)
        assert d.status == "failed" and "400" in d.error
        assert Notification.objects.filter(title__startswith="Courier failed").exists()

    def test_cancel_calls_platform(self, delivery_order, courier_link, fake_courier, user):
        d = self._request(delivery_order, fake_courier, user)
        fake_courier.queue.append((200, {}))
        dispatch.cancel_courier(d, reason="Guest called", by=user)
        assert d.status == "cancelled" and fake_courier.calls[-1]["url"].endswith("/order/WOR-77/status/cancel")

    def test_task_retries_then_fails(self, delivery_order, courier_link, fake_courier, user):
        from apps.ordering import tasks

        d = dispatch.request_courier(delivery_order, provider="wolt_drive", by=user)  # queued (not sync)
        assert d.status == "requested" and not d.external_id
        fake_courier.queue.extend([(503, {"error": "down"})])
        assert tasks.request_courier(str(d.pk)) == "failed"
        d.refresh_from_db()
        assert d.status == "failed"

    def test_not_configured(self, delivery_order, user):
        with pytest.raises(dispatch.DispatchError) as exc:
            dispatch.request_courier(delivery_order, provider="wolt_drive", by=user)
        assert exc.value.code == "not_configured"


G_TOKEN = (200, {"access_token": "g-at", "expires_in": 3600})
G_QUOTE = (
    200,
    {
        "quoteId": "q-1",
        "quotePrice": {"amount": 7.5, "currencyCode": "GEL"},
        "estimatedTimeOfArrival": "2026-09-14T13:10:00Z",
    },
)
G_ORDER = (200, {"trackingNumber": "GLV-1", "state": "NEW", "trackingUrl": "https://glovoapp.com/t/1"})


@pytest.mark.django_db
class TestGlovoOdr:
    def test_token_quote_order(self, delivery_order, courier_link, fake_courier, user):
        fake_courier.queue.extend([G_TOKEN, G_QUOTE, G_ORDER])
        d = dispatch.request_courier(delivery_order, provider="glovo_odr", by=user, sync=True)
        assert d.status == "requested" and d.external_id == "GLV-1" and d.cost == Decimal("7.50")
        token, quote, order = fake_courier.calls
        assert token["url"].endswith("/oauth2/token") and "client_assertion" in token["data"]
        assert quote["url"].endswith("/ge/api/v1/quotes") and quote["headers"]["Authorization"] == "Bearer g-at"
        assert quote["json"]["sender"]["client_vendor_id"] == "vendor-7"
        assert quote["json"]["recipient"]["location"]["latitude"] == 41.72
        assert quote["json"]["client_order_id"] == delivery_order.order_number
        assert order["url"].endswith("/quotes/q-1")

    def test_callback_signature_and_status(self, delivery_order, courier_link, fake_courier, user):
        fake_courier.queue.extend([G_TOKEN, G_QUOTE, G_ORDER])
        d = dispatch.request_courier(delivery_order, provider="glovo_odr", by=user, sync=True)
        payload = {"eventId": "e-1", "trackingNumber": "GLV-1", "state": "PICKED_UP", "courier": {"name": "Lasha"}}
        body = json.dumps(payload).encode()
        sig = hmac.new(b"cb-secret", body, hashlib.sha256).hexdigest()
        res = APIClient().generic("POST", GLOVO_CB, body, content_type="application/json", HTTP_X_SIGNATURE_SHA256=sig)
        assert res.status_code == 200, res.content
        d.refresh_from_db()
        assert d.status == "picked_up" and d.courier_name == "Lasha"
        res = APIClient().generic(
            "POST", GLOVO_CB, body, content_type="application/json", HTTP_X_SIGNATURE_SHA256="bad"
        )
        assert res.status_code == 401

    def test_refresh_polls(self, delivery_order, courier_link, fake_courier, user):
        fake_courier.queue.extend([G_TOKEN, G_QUOTE, G_ORDER])
        d = dispatch.request_courier(delivery_order, provider="glovo_odr", by=user, sync=True)
        fake_courier.queue.append((200, {"trackingNumber": "GLV-1", "state": "DELIVERED"}))
        from apps.ordering import tasks

        assert tasks.refresh_deliveries() == 1
        d.refresh_from_db()
        delivery_order.refresh_from_db()
        assert d.status == "delivered" and delivery_order.status == "completed"


@pytest.mark.django_db
def test_summary_counts(ordering, menu_item, zone, courier, user):
    o = make_delivery_order(ordering, menu_item)
    dispatch.request_courier(o, by=user)
    s = services.summary(ordering)
    assert s["delivery_today"] == 1 and s["in_flight"] == 1 and s["paused"] is False
