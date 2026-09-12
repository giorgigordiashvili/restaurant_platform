"""Wolt integration against hand-written fixtures: auth, client, parser, webhook + fetch, status sync, refunds, menu, venue, dashboard API."""

import hashlib
import hmac
import json
from decimal import Decimal

from django.test import Client

from rest_framework.test import APIClient

import pytest

from apps.delivery import services
from apps.delivery.models import DeliveryPlatformEvent, PlatformMenuSync
from apps.delivery.wolt.auth import WoltAuthError
from apps.delivery.wolt.client import WoltClientError
from apps.delivery.wolt.menu import build_bulk_refresh, build_item_update, build_menu
from apps.delivery.wolt.orders import parse_notification, parse_order
from apps.orders import services as order_services
from apps.orders.models import Order

WEBHOOK = "/api/v1/delivery/wolt/orders/"
TOKEN = (200, {"access_token": "at-1", "expires_in": 3600, "refresh_token": "rt-2", "token_type": "bearer"})


def signed(payload: dict, secret="wolt-secret"):
    body = json.dumps(payload).encode()
    return body, hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def order_json(wolt_fixture, menu_item, **overrides):
    raw = json.dumps(wolt_fixture("order")).replace("{ITEM_ID}", str(menu_item.pk))
    data = json.loads(raw)
    data.update(overrides)
    return data


def post_notification(client, payload, secret="wolt-secret"):
    body, sig = signed(payload, secret)
    return client.generic("POST", WEBHOOK, body, content_type="application/json", HTTP_WOLT_SIGNATURE=sig)


def wolt_order(wolt_link, menu_item, wolt_fixture, fake_wolt, capture, *, order=None, notification=None):
    """Webhook CREATED -> task fetches the order (token + GET) -> auto-accept pushes accept."""
    wolt_link.refresh_from_db()
    if "access_token" not in wolt_link.get_credentials():
        fake_wolt.queue.append(TOKEN)
    fake_wolt.queue.extend([(200, order or order_json(wolt_fixture, menu_item)), (200, {})])
    with capture(execute=True):
        res = post_notification(APIClient(), notification or wolt_fixture("notification"))
    assert res.status_code == 200, res.content
    return Order.objects.get(source="wolt", external_id=(order or {}).get("id", "WO-2001"))


class TestParser:
    def test_order(self, wolt_fixture):
        p = parse_order(wolt_fixture("order"))
        assert p.order_id == "WO-2001" and p.store_id == "venue-1" and p.order_code == "A1B2"
        assert p.products[0].price == Decimal("10.00") and p.products[0].quantity == 2
        assert p.products[0].attributes[0].price == Decimal("1.00") and p.products[0].line_id == "line-1"
        assert p.products[0].attributes[0].name == "Spice extra chili"
        assert p.estimated_total_price == Decimal("23.00") and p.currency == "GEL"
        assert p.delivery_address == "Rustaveli 12, Tbilisi" and p.delivery_type == "homedelivery"
        assert p.estimated_pickup_time.isoformat() == "2026-09-12T12:25:00+00:00"
        assert not p.is_preorder and not p.is_picked_up_by_customer and p.payment_method == "ONLINE"
        assert parse_order({}).order_id == "" and parse_order({"items": [{}]}).products[0].quantity == 1

    def test_notification(self, wolt_fixture):
        n = parse_notification(wolt_fixture("notification"))
        assert n.order_id == "WO-2001" and n.venue_id == "venue-1" and n.status == "CREATED"
        assert parse_notification("junk").order_id == ""


@pytest.mark.django_db
class TestAuth:
    def test_refresh_token_rotates_and_is_cached(self, wolt_link, fake_wolt):
        fake_wolt.queue.extend([TOKEN, (200, {"status": {"is_online": True}}), (200, {})])
        client = services.client_for(wolt_link)
        client.venue_status()
        client.venue_status()
        auth_call = fake_wolt.calls[0]
        assert auth_call["url"].endswith("/oauth2/token") and auth_call["data"] == {
            "grant_type": "refresh_token",
            "refresh_token": "rt-1",
        }
        assert auth_call["headers"]["Authorization"].startswith("Basic ")
        assert fake_wolt.calls[1]["headers"]["Authorization"] == "Bearer at-1"
        assert len(fake_wolt.calls) == 3  # second API call reused the cached token
        wolt_link.refresh_from_db()
        assert wolt_link.get_credentials()["refresh_token"] == "rt-2"

    def test_authorization_code_bootstrap(self, wolt_link, fake_wolt):
        wolt_link.set_credentials({"client_id": "cid", "client_secret": "csec", "authorization_code": "code-1"})
        wolt_link.save()
        fake_wolt.queue.extend([TOKEN, (200, {})])
        services.client_for(wolt_link).venue_status()
        assert fake_wolt.calls[0]["data"]["grant_type"] == "authorization_code"
        creds = wolt_link.get_credentials()
        assert creds["refresh_token"] == "rt-2" and "authorization_code" not in creds

    def test_auth_failure_and_401_retry(self, wolt_link, fake_wolt):
        fake_wolt.queue.append((400, {"error": "invalid_grant"}))
        with pytest.raises(WoltAuthError) as exc:
            services.client_for(wolt_link).venue_status()
        assert not exc.value.retryable
        # expired token -> 401 -> one transparent refresh + retry
        fake_wolt.queue.extend([TOKEN, (401, {}), TOKEN, (200, {"ok": 1})])
        assert services.client_for(wolt_link).venue_status() == {"ok": 1}
        assert [c["url"].endswith("/oauth2/token") for c in fake_wolt.calls[1:]] == [True, False, True, False]

    def test_api_key_mode(self, wolt_link, fake_wolt):
        wolt_link.set_credentials({"api_key": "k-1"})
        wolt_link.save()
        fake_wolt.queue.append((500, {}))
        with pytest.raises(WoltClientError) as exc:
            services.client_for(wolt_link).ready("WO-1")
        assert exc.value.retryable and fake_wolt.calls[0]["headers"]["WOLT-API-KEY"] == "k-1"
        assert fake_wolt.calls[0]["url"].endswith("/orders/WO-1/ready")
        assert "development.dev.woltapi.com" in fake_wolt.calls[0]["url"]  # sandbox

    def test_not_configured(self, wolt_link):
        wolt_link.set_credentials({})
        wolt_link.save()
        assert not services.is_configured(wolt_link)


@pytest.mark.django_db
class TestWebhook:
    def test_created_fetches_and_auto_accepts(
        self, wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks
    ):
        order = wolt_order(wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks)
        assert order.order_type == "delivery" and order.customer_name == "Nino"
        assert order.delivery_address == "Rustaveli 12, Tbilisi" and "ring the bell" in order.customer_notes
        items = {i.item_name: i for i in order.items.all()}
        assert items["ხინკალი"].menu_item == menu_item and items["ხინკალი"].unit_price == Decimal("10.00")
        assert items["ხინკალი"].modifiers.get().price_adjustment == Decimal("1.00")
        assert items["Cake"].menu_item is None and order.total == Decimal("23.00")
        assert "total_mismatch" not in order.platform_data
        assert order.platform_data["lines"] == {str(items["ხინკალი"].pk): "line-1", str(items["Cake"].pk): "line-2"}
        assert order.platform_data["order_code"] == "A1B2"
        order.refresh_from_db()
        assert order.status == "confirmed"
        get_call, accept_call = fake_wolt.calls[1], fake_wolt.calls[2]
        assert get_call["method"] == "GET" and get_call["url"].endswith("/orders/WO-2001")
        assert accept_call["method"] == "PUT" and accept_call["url"].endswith("/orders/WO-2001/accept")
        assert accept_call["json"]["adjusted_pickup_time"].startswith("2026-09-12T")
        ev = DeliveryPlatformEvent.objects.get(event_id="wolt:WO-2001:CREATED")
        assert ev.processed_at and ev.order == order
        push = DeliveryPlatformEvent.objects.get(event_id="wolt:WO-2001:accept")
        assert push.processed_at and push.kind == "status_pushed"
        # replay of the same notification: nothing new
        n = len(fake_wolt.calls)
        with django_capture_on_commit_callbacks(execute=True):
            assert post_notification(APIClient(), wolt_fixture("notification")).json() == {
                "status": "already_processed"
            }
        assert len(fake_wolt.calls) == n and Order.objects.filter(source="wolt").count() == 1

    def test_rejections(self, wolt_link, wolt_fixture):
        c = APIClient()
        note = wolt_fixture("notification")
        assert post_notification(c, note, secret="wrong").status_code == 401
        body, _ = signed(note)
        assert c.generic("POST", WEBHOOK, body, content_type="application/json").status_code == 401
        other = {**note, "order": {**note["order"], "venue_id": "nope"}}
        assert post_notification(c, other).status_code == 404
        assert c.generic("POST", WEBHOOK, b"{", content_type="application/json").status_code == 400
        wolt_link.is_enabled = False
        wolt_link.save()
        assert post_notification(c, note).status_code == 503

    def test_manual_accept(self, wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks):
        wolt_link.auto_accept = False
        wolt_link.save()
        fake_wolt.queue.extend([TOKEN, (200, order_json(wolt_fixture, menu_item))])
        with django_capture_on_commit_callbacks(execute=True):
            post_notification(APIClient(), wolt_fixture("notification"))
        order = Order.objects.get(source="wolt")
        assert order.status == "pending" and len(fake_wolt.calls) == 2
        fake_wolt.queue.append((200, {}))
        with django_capture_on_commit_callbacks(execute=True):
            order_services.transition_order(order, "confirmed")
        assert fake_wolt.calls[-1]["url"].endswith("/orders/WO-2001/accept")

    def test_preorder_confirm_then_production(
        self, wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks
    ):
        payload = order_json(
            wolt_fixture,
            menu_item,
            type="preorder",
            pre_order={"preorder_time": "2026-09-12T18:00:00Z", "pre_order_status": "waiting"},
        )
        order = wolt_order(
            wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks, order=payload
        )
        assert order.status == "pending" and order.platform_data["is_preorder"]
        assert fake_wolt.calls[-1]["url"].endswith("/orders/WO-2001/confirm-preorder")
        assert "Pre-order for" in order.customer_notes
        note = wolt_fixture("notification")
        note["order"]["status"] = "PRODUCTION"
        note["id"] = "notif-2"
        n = len(fake_wolt.calls)
        with django_capture_on_commit_callbacks(execute=True):
            assert post_notification(APIClient(), note).status_code == 200
        order.refresh_from_db()
        assert order.status == "confirmed" and len(fake_wolt.calls) == n  # already confirmed on Wolt
        assert order.platform_data["platform_status"] == "production"

    def test_rejected_and_delivered_notifications(
        self, wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks
    ):
        order = wolt_order(wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks)
        note = wolt_fixture("notification")
        note["order"]["status"] = "DELIVERED"
        with django_capture_on_commit_callbacks(execute=True):
            post_notification(APIClient(), note)
        order.refresh_from_db()
        assert order.status == "completed"
        # a second Wolt order gets rejected (cancelled by Wolt support / customer)
        payload = order_json(wolt_fixture, menu_item, id="WO-2002")
        note2 = {**wolt_fixture("notification"), "id": "notif-3", "order": {**note["order"], "id": "WO-2002"}}
        note2["order"]["status"] = "CREATED"
        order2 = wolt_order(
            wolt_link,
            menu_item,
            wolt_fixture,
            fake_wolt,
            django_capture_on_commit_callbacks,
            order=payload,
            notification=note2,
        )
        note2["order"]["status"] = "REJECTED"
        n = len(fake_wolt.calls)
        with django_capture_on_commit_callbacks(execute=True):
            post_notification(APIClient(), note2)
        order2.refresh_from_db()
        assert order2.status == "cancelled" and "Wolt" in order2.cancellation_reason
        assert len(fake_wolt.calls) == n  # no reject pushed back for a platform-side rejection
        # unknown order + REJECTED -> processed with error, no fetch
        note3 = {**note2, "id": "notif-4", "order": {**note2["order"], "id": "WO-404"}}
        with django_capture_on_commit_callbacks(execute=True):
            post_notification(APIClient(), note3)
        assert DeliveryPlatformEvent.objects.get(event_id="wolt:WO-404:REJECTED").error == "unknown order"
        assert len(fake_wolt.calls) == n

    def test_fetch_failure_is_recorded(self, wolt_link, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks):
        fake_wolt.queue.extend([TOKEN, (500, {"error": "boom"})])
        with django_capture_on_commit_callbacks(execute=True):
            assert post_notification(APIClient(), wolt_fixture("notification")).status_code == 200
        ev = DeliveryPlatformEvent.objects.get(event_id="wolt:WO-2001:CREATED")
        assert ev.processed_at is None and "500" in ev.error and not Order.objects.filter(source="wolt").exists()


@pytest.mark.django_db
class TestStatusSync:
    def test_ready_delivered_reject(
        self, wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks
    ):
        order = wolt_order(wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks)
        fake_wolt.queue.append((200, {}))
        with django_capture_on_commit_callbacks(execute=True):
            order_services.transition_order(order, "ready")
        assert fake_wolt.calls[-1]["url"].endswith("/orders/WO-2001/ready")
        n = len(fake_wolt.calls)
        with django_capture_on_commit_callbacks(execute=True):
            order_services.transition_order(order, "completed")
        assert len(fake_wolt.calls) == n  # courier order: Wolt marks it delivered
        # takeaway order: we tell Wolt it was handed over
        payload = order_json(wolt_fixture, menu_item, id="WO-2003")
        payload["delivery"]["type"] = "takeaway"
        note = {
            **wolt_fixture("notification"),
            "id": "n5",
            "order": {**wolt_fixture("notification")["order"], "id": "WO-2003"},
        }
        take = wolt_order(
            wolt_link,
            menu_item,
            wolt_fixture,
            fake_wolt,
            django_capture_on_commit_callbacks,
            order=payload,
            notification=note,
        )
        assert take.order_type == "takeaway"
        fake_wolt.queue.append((200, {}))
        with django_capture_on_commit_callbacks(execute=True):
            order_services.transition_order(take, "completed")
        assert fake_wolt.calls[-1]["url"].endswith("/orders/WO-2003/delivered")

    def test_reject_only_before_acceptance(
        self, wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks
    ):
        wolt_link.auto_accept = False
        wolt_link.save()
        fake_wolt.queue.extend([TOKEN, (200, order_json(wolt_fixture, menu_item))])
        with django_capture_on_commit_callbacks(execute=True):
            post_notification(APIClient(), wolt_fixture("notification"))
        order = Order.objects.get(source="wolt")
        fake_wolt.queue.append((200, {}))
        with django_capture_on_commit_callbacks(execute=True):
            order_services.transition_order(order, "cancelled", cancellation_reason="Out of khinkali")
        call = fake_wolt.calls[-1]
        assert call["url"].endswith("/orders/WO-2001/reject") and call["json"] == {"reason": "Out of khinkali"}
        assert not DeliveryPlatformEvent.objects.filter(kind="cancel_requested").exists()

    def test_cancel_after_acceptance_records_request(
        self, wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks
    ):
        order = wolt_order(wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks)
        n = len(fake_wolt.calls)
        with django_capture_on_commit_callbacks(execute=True):
            order_services.transition_order(order, "cancelled", cancellation_reason="Kitchen fire")
        assert len(fake_wolt.calls) == n
        assert DeliveryPlatformEvent.objects.filter(kind="cancel_requested", order=order).exists()

    def test_retryable_and_final_errors(
        self, wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks
    ):
        order = wolt_order(wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks)
        fake_wolt.queue.append((503, {}))
        with pytest.raises(WoltClientError):
            services.push_status(order, "ready")
        ev = DeliveryPlatformEvent.objects.get(event_id="wolt:WO-2001:ready")
        assert ev.processed_at is None
        fake_wolt.queue.append((409, {"detail": "already ready"}))
        services.push_status(order, "ready")
        ev.refresh_from_db()
        assert ev.processed_at and "not retried" in ev.error


@pytest.mark.django_db
class TestRefunds:
    def test_void_pushes_refund(
        self, wolt_link, menu_item, wolt_fixture, fake_wolt, user, django_capture_on_commit_callbacks
    ):
        order = wolt_order(wolt_link, menu_item, wolt_fixture, fake_wolt, django_capture_on_commit_callbacks)
        cake = order.items.get(item_name="Cake")
        fake_wolt.queue.append((200, {"refunded": True}))
        with django_capture_on_commit_callbacks(execute=True):
            order_services.void_item(cake, by=user, reason_text="Dropped it")
        call = fake_wolt.calls[-1]
        assert call["method"] == "POST" and call["url"].endswith("/orders/WO-2001/refund-items")
        assert call["json"] == {"items": [{"id": "line-2", "count": 1}]}
        ev = DeliveryPlatformEvent.objects.get(kind="refund_pushed")
        assert ev.processed_at and ev.event_id == f"wolt:WO-2001:refund:{cake.pk}"
        # voiding again is a no-op; voiding a pending (unaccepted) order's line never refunds
        n = len(fake_wolt.calls)
        with django_capture_on_commit_callbacks(execute=True):
            order_services.void_item(cake, by=user, reason_text="again")
        assert len(fake_wolt.calls) == n


@pytest.mark.django_db
class TestMenu:
    def test_build_menu(self, wolt_link, menu_item, menu_category, create_menu_item, restaurant):
        from apps.menu.models import MenuItemModifierGroup, Modifier, ModifierGroup

        group = ModifierGroup.objects.create(
            restaurant=restaurant, selection_type="multiple", min_selections=0, max_selections=2
        )
        group.set_current_language("en")
        group.name = "Extras"
        group.save()
        m1 = Modifier.objects.create(group=group, price_adjustment=Decimal("1.50"))
        m1.set_current_language("en")
        m1.name = "Cheese"
        m1.save()
        MenuItemModifierGroup.objects.create(menu_item=menu_item, modifier_group=group)
        sold_out = create_menu_item(restaurant=restaurant, category=menu_category, name="Sold out", price=Decimal("3"))
        sold_out.auto_disabled_by_stock = True
        sold_out.save()
        menu = build_menu(wolt_link)
        assert menu["currency"] == "GEL" and menu["primary_language"] in ("ka", "en")
        cat = menu["categories"][0]
        items = {i["external_data"]: i for i in cat["items"]}
        item = items[f"p{menu_item.pk}"]
        assert item["price"] == float(menu_item.price) and item["enabled"] is True
        assert {"lang": "en", "value": menu_item.safe_translation_getter("name", language_code="en")} in item["name"]
        assert item["options"] == [
            {
                "name": [{"lang": "en", "value": "Extras"}],
                "type": "MultiChoice",
                "selection_range": {"min": 0, "max": 2},
                "external_data": f"g{group.pk}",
                "values": [
                    {
                        "name": [{"lang": "en", "value": "Cheese"}],
                        "price": 1.5,
                        "enabled": True,
                        "default": False,
                        "external_data": f"m{m1.pk}",
                    }
                ],
            }
        ]
        assert items[f"p{sold_out.pk}"]["enabled"] is False
        assert build_item_update(m1, False) == ("options", [{"external_id": f"m{m1.pk}", "enabled": False}])
        assert build_item_update(menu_item, True) == (
            "items",
            [{"external_id": f"p{menu_item.pk}", "enabled": True, "in_stock": True}],
        )
        refresh = build_bulk_refresh(restaurant)
        assert {
            "external_id": f"p{menu_item.pk}",
            "enabled": True,
            "in_stock": True,
            "price": int(menu_item.price * 100),
        } in refresh["items"]
        assert refresh["options"] == [{"external_id": f"m{m1.pk}", "enabled": True, "price": 150}]

    def test_push_menu_and_updates(self, wolt_link, menu_item, fake_wolt, user, django_capture_on_commit_callbacks):
        fake_wolt.queue.extend([TOKEN, (202, {})])
        with django_capture_on_commit_callbacks(execute=True):
            sync = services.start_menu_sync(wolt_link, by=user)
        sync.refresh_from_db()
        assert sync.status == "success" and sync.product_count == 1
        call = fake_wolt.calls[-1]
        assert call["method"] == "POST" and call["url"].endswith("/v1/restaurants/venue-1/menu")
        assert call["json"]["categories"][0]["items"][0]["external_data"] == f"p{menu_item.pk}"
        wolt_link.refresh_from_db()
        assert wolt_link.last_menu_sync_status == "success"
        fake_wolt.queue.append((200, {}))
        with django_capture_on_commit_callbacks(execute=True):
            sync = services.start_menu_sync(wolt_link, by=user, kind="updates")
        sync.refresh_from_db()
        assert sync.status == "success" and sync.request["kind"] == "updates"
        call = fake_wolt.calls[-1]
        assert call["method"] == "PATCH" and call["url"].endswith("/venues/venue-1/items")
        assert call["json"]["data"][0]["external_id"] == f"p{menu_item.pk}"
        fake_wolt.queue.append((500, {}))
        with django_capture_on_commit_callbacks(execute=True):
            sync = services.start_menu_sync(wolt_link, by=user)
        sync.refresh_from_db()
        assert sync.status == "failed" and "500" in sync.error

    def test_adapter(self, wolt_link, menu_item, fake_wolt):
        from apps.inventory.platforms import get_adapter

        fake_wolt.queue.extend([TOKEN, (200, {})])
        result = get_adapter(wolt_link).set_item_availability(wolt_link, menu_item, False)
        assert result.ok and not result.manual and result.external_ref == f"p{menu_item.pk}"
        assert fake_wolt.calls[-1]["json"] == {
            "data": [{"external_id": f"p{menu_item.pk}", "enabled": False, "in_stock": False}]
        }
        fake_wolt.queue.append((500, {}))
        assert not get_adapter(wolt_link).set_item_availability(wolt_link, menu_item, True).ok


@pytest.mark.django_db
class TestVenue:
    def test_pause_resume_status(self, wolt_link, fake_wolt, wolt_fixture, user):
        fake_wolt.queue.extend([TOKEN, (200, {})])
        ev = services.pause_store(wolt_link, 45, by=user)
        call = fake_wolt.calls[-1]
        assert call["method"] == "PATCH" and call["url"].endswith("/venues/venue-1/online")
        assert call["json"]["status"] == "OFFLINE" and call["json"]["until"]
        assert ev.kind == "store_status" and ev.payload["status"] == "paused"
        wolt_link.refresh_from_db()
        assert wolt_link.store_paused_until is not None
        fake_wolt.queue.append((200, wolt_fixture("venue_status")))
        status = services.store_status(wolt_link)
        assert status["paused_until"] and status["live"]["is_online"] is True and status["online"] is True
        fake_wolt.queue.append((200, {}))
        services.resume_store(wolt_link, by=user)
        assert fake_wolt.calls[-1]["json"] == {"status": "ONLINE"}
        wolt_link.refresh_from_db()
        assert wolt_link.store_paused_until is None
        fake_wolt.queue.append((502, {}))
        with pytest.raises(services.DeliveryError):
            services.pause_store(wolt_link, 10)


@pytest.mark.django_db
class TestDashboardApi:
    def _owner(self, user, restaurant, staff_roles, create_staff_member):
        create_staff_member(user=user, restaurant=restaurant, role=next(r for r in staff_roles if r.name == "owner"))

    def test_platform_list_pause_resume(
        self,
        authenticated_owner_client,
        user,
        delivery_restaurant,
        staff_roles,
        create_staff_member,
        wolt_link,
        fake_wolt,
        wolt_fixture,
    ):
        api_client = authenticated_owner_client
        api_client.defaults["HTTP_X_RESTAURANT"] = delivery_restaurant.slug
        self._owner(user, delivery_restaurant, staff_roles, create_staff_member)
        res = api_client.get("/api/v1/dashboard/delivery/platforms/")
        assert res.status_code == 200, res.content
        rows = {r["platform"]: r for r in res.json()}
        assert rows["wolt"]["configured"] and rows["wolt"]["implemented"] and rows["wolt"]["online"]
        assert rows["bolt_food"]["implemented"] is False and rows["glovo"]["configured"] is False
        fake_wolt.queue.extend([TOKEN, (200, {}), (200, wolt_fixture("venue_status"))])
        res = api_client.post("/api/v1/dashboard/delivery/platforms/wolt/pause/", {"minutes": 15}, format="json")
        assert res.status_code == 200, res.content
        assert res.json()["paused_until"] and res.json()["online"] is True  # Wolt still reports online in fixture
        fake_wolt.queue.extend([(200, {}), (200, wolt_fixture("venue_status"))])
        assert api_client.post("/api/v1/dashboard/delivery/platforms/wolt/resume/").status_code == 200
        fake_wolt.queue.append((503, {}))
        res = api_client.post("/api/v1/dashboard/delivery/platforms/wolt/pause/", {"minutes": 15}, format="json")
        assert res.status_code == 502 and res.json()["error"]["code"] == "platform_error"
        assert api_client.post("/api/v1/dashboard/delivery/platforms/nope/pause/").status_code == 404
        fake_wolt.queue.append((202, {}))
        res = api_client.post("/api/v1/dashboard/delivery/platforms/wolt/menu-sync/", {"kind": "full"}, format="json")
        assert res.status_code == 202 and res.json()["status"] in ("queued", "success")  # runs on commit
        assert api_client.post("/api/v1/dashboard/delivery/platforms/bolt_food/menu-sync/").status_code == 400

    def test_module_off_404(
        self, authenticated_owner_client, user, delivery_restaurant, staff_roles, create_staff_member, wolt_link
    ):
        api_client = authenticated_owner_client
        api_client.defaults["HTTP_X_RESTAURANT"] = delivery_restaurant.slug
        self._owner(user, delivery_restaurant, staff_roles, create_staff_member)
        delivery_restaurant.delivery_enabled = False
        delivery_restaurant.save(update_fields=["delivery_enabled"])
        assert api_client.get("/api/v1/dashboard/delivery/platforms/").status_code in (403, 404)


@pytest.mark.django_db
class TestAdminPage:
    def test_wolt_card_and_actions(
        self, user, delivery_restaurant, staff_roles, create_staff_member, wolt_link, fake_wolt, wolt_fixture
    ):
        create_staff_member(
            user=user, restaurant=delivery_restaurant, role=next(r for r in staff_roles if r.name == "owner")
        )
        c = Client(HTTP_HOST=f"{delivery_restaurant.slug}.localhost")
        c.force_login(user)
        base = "/tenant-admin/delivery/deliveryplatformspage/"
        page = c.get(base)
        assert page.status_code == 200
        html = page.content.decode()
        assert 'data-testid="platform-wolt"' in html and "/api/v1/delivery/wolt/orders/" in html
        assert 'name="client_secret"' in html and "Pause orders" in html
        res = c.post(
            f"{base}wolt/save/",
            {
                "store_external_id": "venue-9",
                "client_id": "new-id",
                "client_secret": "",
                "is_enabled": "1",
                "auto_accept": "1",
                "prep_time_minutes": "25",
            },
        )
        assert res.status_code == 302
        wolt_link.refresh_from_db()
        creds = wolt_link.get_credentials()
        assert wolt_link.store_external_id == "venue-9" and creds["client_id"] == "new-id"
        assert "access_token" not in creds
        assert creds["client_secret"] == "csec" and creds["refresh_token"] == "rt-1"  # blanks keep secrets
        assert c.post(f"{base}wolt/rotate-webhook-token/").status_code == 302
        assert 'data-testid="webhook-token-once"' in c.get(base).content.decode()
        fake_wolt.queue.extend([TOKEN, (200, {})])
        assert c.post(f"{base}wolt/pause/", {"minutes": "30"}).status_code == 302
        assert 'data-testid="paused-badge"' in c.get(base).content.decode()
        fake_wolt.queue.append((200, {}))
        assert c.post(f"{base}wolt/resume/").status_code == 302
        fake_wolt.queue.append((200, wolt_fixture("venue_status")))
        assert c.post(f"{base}wolt/check/").status_code == 302
        fake_wolt.queue.append((200, {}))
        assert c.post(f"{base}wolt/sync-updates/").status_code == 302
        assert PlatformMenuSync.objects.filter(link=wolt_link, request__kind="updates").exists()
        assert c.post(f"{base}bolt_food/pause/").status_code == 403
        res = c.post(f"{base}wolt/save/", {"store_external_id": "venue-9", "clear_credentials": "1"})
        wolt_link.refresh_from_db()
        assert wolt_link.get_credentials() == {}
