"""Glovo integration against hand-written fixtures: menu builder, webhooks, status sync, adapter, client, admin."""

import json
from decimal import Decimal

from django.test import Client

import pytest

from apps.delivery import services
from apps.delivery.config import GlovoConfig
from apps.delivery.glovo.client import GlovoClient, GlovoClientError
from apps.delivery.glovo.menu import build_menu
from apps.delivery.glovo.orders import money_from_minor, parse_order
from apps.delivery.models import DeliveryPlatformEvent, PlatformMenuSync
from apps.orders import services as order_services
from apps.orders.models import Order

WEBHOOK = "/api/v1/delivery/glovo/orders/"


def order_payload(glovo_fixture, menu_item):
    raw = json.dumps(glovo_fixture("order_notification")).replace("{ITEM_ID}", str(menu_item.pk))
    return json.loads(raw)


@pytest.mark.django_db
class TestMenuBuilder:
    def test_menu_json_shape(self, glovo_link, menu_item, menu_category, create_menu_item, restaurant):
        from apps.menu.models import MenuItemModifierGroup, Modifier, ModifierGroup

        group = ModifierGroup.objects.create(
            restaurant=restaurant, selection_type="single", min_selections=0, max_selections=1
        )
        group.set_current_language("en")
        group.name = "Spice"
        group.save()
        m1 = Modifier.objects.create(group=group, price_adjustment=Decimal("1.00"))
        m1.set_current_language("en")
        m1.name = "Chili"
        m1.save()
        m2 = Modifier.objects.create(group=group, price_adjustment=Decimal("0"), is_available=False)
        m2.set_current_language("en")
        m2.name = "Mild"
        m2.save()
        MenuItemModifierGroup.objects.create(menu_item=menu_item, modifier_group=group)
        sold_out = create_menu_item(restaurant=restaurant, category=menu_category, name="Sold out", price=Decimal("3"))
        sold_out.auto_disabled_by_stock = True
        sold_out.save()
        menu = build_menu(glovo_link)
        products = {p["id"]: p for p in menu["products"]}
        assert products[f"p{menu_item.pk}"]["price"] == float(menu_item.price)
        assert products[f"p{menu_item.pk}"]["attributes_groups"] == [f"g{group.pk}"]
        assert products[f"p{sold_out.pk}"]["available"] is False
        assert menu["attribute_groups"][0] == {
            "id": f"g{group.pk}",
            "name": "Spice",
            "min": 0,
            "max": 1,
            "multiple_selection": False,
            "attributes": [f"m{m1.pk}", f"m{m2.pk}"],
        }
        attrs = {a["id"]: a for a in menu["attributes"]}
        assert attrs[f"m{m1.pk}"]["price_impact"] == 1.0 and attrs[f"m{m2.pk}"]["available"] is False
        assert menu["collections"][0]["sections"][0]["products"] == [f"p{menu_item.pk}", f"p{sold_out.pk}"]

    def test_menu_feed_view(self, api_client, glovo_link, menu_item):
        ok = api_client.get(f"/api/v1/delivery/glovo/menu/{glovo_link.pk}/mt/")
        assert ok.status_code == 200 and ok.json()["products"][0]["id"] == f"p{menu_item.pk}"
        assert api_client.get(f"/api/v1/delivery/glovo/menu/{glovo_link.pk}/wrong/").status_code == 404


class TestParser:
    def test_parse_tolerant(self, glovo_fixture):
        parsed = parse_order(glovo_fixture("order_notification"))
        assert parsed.order_id == "GL-1001" and parsed.order_code == "A1B2C"
        assert parsed.products[0].price == Decimal("10.00") and parsed.products[0].attributes[0].price == Decimal(
            "1.00"
        )
        assert parsed.estimated_total_price == Decimal("26.00") and parsed.total_customer_to_pay == Decimal("29.00")
        assert parsed.estimated_pickup_time.isoformat() == "2026-09-12T12:25:00+00:00"
        assert parse_order({}).order_id == "" and parse_order({"products": [{}]}).products[0].quantity == 1
        assert money_from_minor(None) == Decimal("0")


@pytest.mark.django_db
class TestWebhooks:
    def test_order_created_auto_accept_and_status_push(
        self, api_client, glovo_link, menu_item, glovo_fixture, fake_glovo, django_capture_on_commit_callbacks
    ):
        fake_glovo.queue.append((200, {"status": "ACCEPTED"}))
        payload = order_payload(glovo_fixture, menu_item)
        with django_capture_on_commit_callbacks(execute=True):
            res = api_client.post(WEBHOOK, payload, format="json", HTTP_AUTHORIZATION="tok")
        assert res.status_code == 200, res.content
        order = Order.objects.get(pk=res.json()["order_id"])
        assert order.source == "glovo" and order.external_id == "GL-1001" and order.order_type == "delivery"
        assert (
            order.customer_name == "Nino"
            and "Pick-up code 7788" in order.customer_notes
            and "Allergy: no nuts" in order.customer_notes
        )
        items = {i.item_name: i for i in order.items.all()}
        assert items["ხინკალი"].menu_item == menu_item and items["ხინკალი"].unit_price == Decimal("10.00")
        assert items["ხინკალი"].modifiers.get().price_adjustment == Decimal("1.00") and items[
            "ხინკალი"
        ].total_price == Decimal("22.00")
        assert items["Cake"].menu_item is None and items["Cake"].unit_price == Decimal("5.00")
        assert order.total == Decimal("27.00")
        assert (
            order.platform_data["order_code"] == "A1B2C"
            and order.platform_data["total_mismatch"]["platform"] == "26.00"
        )
        order.refresh_from_db()
        assert order.status == "confirmed"  # auto-accepted after commit
        push = DeliveryPlatformEvent.objects.get(kind="status_pushed")
        assert push.processed_at and push.event_id == "glovo:GL-1001:ACCEPTED"
        call = fake_glovo.calls[-1]
        assert call["method"] == "PUT" and call["url"].endswith("/webhook/stores/store-1/orders/GL-1001/status")
        assert call["json"] == {"status": "ACCEPTED"} and call["headers"]["Authorization"] == "x-token"
        # replay -> same order, nothing new
        with django_capture_on_commit_callbacks(execute=True):
            again = api_client.post(WEBHOOK, payload, format="json", HTTP_AUTHORIZATION="tok")
        assert again.status_code == 200 and again.json()["order_id"] == str(order.pk)
        assert Order.objects.filter(source="glovo").count() == 1

    def test_rejections(self, api_client, glovo_link, menu_item, glovo_fixture):
        payload = order_payload(glovo_fixture, menu_item)
        assert api_client.post(WEBHOOK, payload, format="json", HTTP_AUTHORIZATION="nope").status_code == 401
        assert (
            api_client.post(
                WEBHOOK, {**payload, "store_id": "other"}, format="json", HTTP_AUTHORIZATION="tok"
            ).status_code
            == 404
        )
        assert api_client.post(WEBHOOK, [1, 2], format="json", HTTP_AUTHORIZATION="tok").status_code == 400
        glovo_link.restaurant.delivery_enabled = False
        glovo_link.restaurant.save(update_fields=["delivery_enabled"])
        assert api_client.post(WEBHOOK, payload, format="json", HTTP_AUTHORIZATION="tok").status_code == 503

    def test_manual_accept_when_auto_accept_off(
        self, api_client, glovo_link, menu_item, glovo_fixture, fake_glovo, django_capture_on_commit_callbacks
    ):
        glovo_link.auto_accept = False
        glovo_link.save()
        with django_capture_on_commit_callbacks(execute=True):
            res = api_client.post(
                WEBHOOK, order_payload(glovo_fixture, menu_item), format="json", HTTP_AUTHORIZATION="tok"
            )
        order = Order.objects.get(pk=res.json()["order_id"])
        assert order.status == "pending" and not fake_glovo.calls
        fake_glovo.queue.append((200, {}))
        with django_capture_on_commit_callbacks(execute=True):
            order_services.transition_order(order, "confirmed")
        assert fake_glovo.calls[-1]["json"] == {"status": "ACCEPTED"}

    def test_cancel_webhook(
        self, api_client, glovo_link, menu_item, glovo_fixture, fake_glovo, django_capture_on_commit_callbacks
    ):
        fake_glovo.queue.append((200, {}))
        with django_capture_on_commit_callbacks(execute=True):
            res = api_client.post(
                WEBHOOK, order_payload(glovo_fixture, menu_item), format="json", HTTP_AUTHORIZATION="tok"
            )
        order = Order.objects.get(pk=res.json()["order_id"])
        with django_capture_on_commit_callbacks(execute=True):
            res = api_client.post(
                f"{WEBHOOK}GL-1001/cancel/", glovo_fixture("order_cancel"), format="json", HTTP_AUTHORIZATION="tok"
            )
        assert res.status_code == 200 and res.json()["status"] == "cancelled"
        order.refresh_from_db()
        assert order.status == "cancelled" and "Customer changed mind" in order.cancellation_reason
        res = api_client.post(
            f"{WEBHOOK}GL-404/cancel/", {"store_id": "store-1"}, format="json", HTTP_AUTHORIZATION="tok"
        )
        assert res.status_code == 200 and res.json()["status"] == "unknown_order"


@pytest.mark.django_db
class TestStatusSync:
    def _glovo_order(self, glovo_link, menu_item, glovo_fixture, api_client, fake_glovo, capture):
        fake_glovo.queue.append((200, {}))
        with capture(execute=True):
            res = api_client.post(
                WEBHOOK, order_payload(glovo_fixture, menu_item), format="json", HTTP_AUTHORIZATION="tok"
            )
        return Order.objects.get(pk=res.json()["order_id"])

    def test_ready_pushes_and_is_idempotent(
        self, api_client, glovo_link, menu_item, glovo_fixture, fake_glovo, django_capture_on_commit_callbacks
    ):
        order = self._glovo_order(
            glovo_link, menu_item, glovo_fixture, api_client, fake_glovo, django_capture_on_commit_callbacks
        )
        fake_glovo.queue.append((200, {}))
        with django_capture_on_commit_callbacks(execute=True):
            order_services.transition_order(order, "ready")
        assert fake_glovo.calls[-1]["json"] == {"status": "READY_FOR_PICKUP"}
        n = len(fake_glovo.calls)
        services.push_status(order, "READY_FOR_PICKUP")  # already processed -> no call
        assert len(fake_glovo.calls) == n
        with django_capture_on_commit_callbacks(execute=True):
            order_services.transition_order(order, "served")
        assert len(fake_glovo.calls) == n  # OUT_FOR_DELIVERY never sent

    def test_400_on_accepted_is_logged_not_retried(
        self, api_client, glovo_link, menu_item, glovo_fixture, fake_glovo, django_capture_on_commit_callbacks
    ):
        fake_glovo.queue.append((400, {"error": "order acceptance disabled"}))
        order = self._glovo_order(
            glovo_link, menu_item, glovo_fixture, api_client, fake_glovo, django_capture_on_commit_callbacks
        )
        order.refresh_from_db()
        assert order.status == "confirmed"
        ev = DeliveryPlatformEvent.objects.get(kind="status_pushed")
        assert ev.processed_at and "not retried" in ev.error

    def test_500_raises_retryable(
        self, glovo_link, menu_item, glovo_fixture, api_client, fake_glovo, django_capture_on_commit_callbacks
    ):
        order = self._glovo_order(
            glovo_link, menu_item, glovo_fixture, api_client, fake_glovo, django_capture_on_commit_callbacks
        )
        fake_glovo.queue.append((500, {"error": "boom"}))
        with pytest.raises(GlovoClientError) as exc:
            services.push_status(order, "READY_FOR_PICKUP")
        assert exc.value.retryable
        ev = DeliveryPlatformEvent.objects.get(event_id="glovo:GL-1001:READY_FOR_PICKUP")
        assert ev.processed_at is None and "500" in ev.error

    def test_restaurant_cancel_records_event(
        self, api_client, glovo_link, menu_item, glovo_fixture, fake_glovo, django_capture_on_commit_callbacks
    ):
        order = self._glovo_order(
            glovo_link, menu_item, glovo_fixture, api_client, fake_glovo, django_capture_on_commit_callbacks
        )
        with django_capture_on_commit_callbacks(execute=True):
            order_services.transition_order(order, "cancelled", cancellation_reason="Out of stock")
        assert DeliveryPlatformEvent.objects.filter(kind="cancel_requested", order=order).exists()


@pytest.mark.django_db
class TestAdapterAndMenuPush:
    def test_adapter_sends_bulk_update(self, glovo_link, menu_item, fake_glovo, glovo_fixture):
        from apps.inventory.platforms import get_adapter

        fake_glovo.queue.append((202, glovo_fixture("bulk_update_202")))
        result = get_adapter(glovo_link).set_item_availability(glovo_link, menu_item, False)
        assert result.ok and not result.manual and result.external_ref == "tx-bulk-1"
        assert fake_glovo.calls[-1]["json"] == {
            "products": [{"id": f"p{menu_item.pk}", "available": False}],
            "attributes": [],
        }
        fake_glovo.queue.append((500, {}))
        assert not get_adapter(glovo_link).set_item_availability(glovo_link, menu_item, True).ok

    def test_module_off_means_manual(self, glovo_link, menu_item):
        from apps.inventory.platforms import ManualAdapter, get_adapter

        glovo_link.restaurant.delivery_enabled = False
        assert isinstance(get_adapter(glovo_link), ManualAdapter)

    def test_push_menu_flow(
        self, glovo_link, menu_item, fake_glovo, glovo_fixture, user, django_capture_on_commit_callbacks
    ):
        fake_glovo.queue.append((202, glovo_fixture("menu_upload_202")))
        fake_glovo.queue.append((200, glovo_fixture("menu_status_success")))
        with django_capture_on_commit_callbacks(execute=True):
            sync = services.start_menu_sync(glovo_link, by=user)
        sync.refresh_from_db()
        assert sync.status == "success" and sync.transaction_id == "tx-123" and sync.product_count == 1
        assert fake_glovo.calls[0]["json"]["menuUrl"].endswith(f"/api/v1/delivery/glovo/menu/{glovo_link.pk}/mt/")
        glovo_link.refresh_from_db()
        assert glovo_link.last_menu_sync_status == "success"


class TestClient:
    def test_headers_and_errors(self):
        from tests.delivery.conftest import FakeSession

        session = FakeSession((429, {"e": 1}), (404, {"e": 2}))
        client = GlovoClient(
            GlovoConfig(api_token="t", store_id="s", base_url="https://x", auth_scheme="Bearer"), session=session
        )
        with pytest.raises(GlovoClientError) as exc:
            client.open_store()
        assert exc.value.retryable and session.calls[0]["headers"]["Authorization"] == "Bearer t"
        with pytest.raises(GlovoClientError) as exc:
            client.close_store("2026-09-12T20:00:00Z")
        assert not exc.value.retryable and exc.value.status_code == 404


@pytest.mark.django_db
class TestAdminPage:
    def test_page_and_actions(
        self, user, delivery_restaurant, staff_roles, create_staff_member, glovo_link, fake_glovo, glovo_fixture
    ):
        create_staff_member(
            user=user, restaurant=delivery_restaurant, role=next(r for r in staff_roles if r.name == "owner")
        )
        user.is_staff = True
        user.save()
        c = Client(HTTP_HOST=f"{delivery_restaurant.slug}.localhost")
        c.force_login(user)
        page = "/tenant-admin/delivery/deliveryplatformspage/"
        html = c.get(page).content.decode()
        assert (
            'data-testid="platform-glovo"' in html
            and "Coming soon" in html
            and "/api/v1/delivery/glovo/orders/" in html
        )
        resp = c.post(
            page + "glovo/save/",
            {
                "store_external_id": "store-9",
                "is_enabled": "1",
                "auto_accept": "1",
                "prep_time_minutes": "25",
                "api_token": "new-token",
            },
        )
        assert resp.status_code == 302
        glovo_link.refresh_from_db()
        assert glovo_link.store_external_id == "store-9" and glovo_link.prep_time_minutes == 25
        assert (
            glovo_link.get_credentials()["api_token"] == "new-token" and "new-token" not in c.get(page).content.decode()
        )
        resp = c.post(page + "glovo/rotate-webhook-token/")
        html = c.get(page).content.decode()
        glovo_link.refresh_from_db()
        assert 'data-testid="webhook-token-once"' in html and glovo_link.webhook_token in html
        assert glovo_link.webhook_token not in c.get(page).content.decode()  # shown once
        fake_glovo.queue.append((202, glovo_fixture("menu_upload_202")))
        fake_glovo.queue.append((200, glovo_fixture("menu_status_success")))
        assert c.post(page + "glovo/push-menu/").status_code == 302
        assert PlatformMenuSync.objects.filter(link=glovo_link).exists()
        dash = c.get("/tenant-admin/").content.decode()
        assert 'data-testid="card-delivery"' in dash
        delivery_restaurant.delivery_enabled = False
        delivery_restaurant.save(update_fields=["delivery_enabled"])
        assert c.get(page).status_code == 403

    def test_orders_api_exposes_source(
        self,
        authenticated_owner_client,
        glovo_link,
        menu_item,
        glovo_fixture,
        fake_glovo,
        django_capture_on_commit_callbacks,
    ):
        from rest_framework.test import APIClient

        fake_glovo.queue.append((200, {}))
        with django_capture_on_commit_callbacks(execute=True):
            res = APIClient().post(
                WEBHOOK, order_payload(glovo_fixture, menu_item), format="json", HTTP_AUTHORIZATION="tok"
            )
        assert res.status_code == 200, res.content
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = glovo_link.restaurant.slug
        res = authenticated_owner_client.get("/api/v1/dashboard/orders/?source=glovo")
        assert res.status_code == 200 and res.json()["count"] == 1 and res.json()["results"][0]["source"] == "glovo"
        res = authenticated_owner_client.get("/api/v1/dashboard/orders/kitchen/")
        row = res.json()["results"][0]
        assert row["source"] == "glovo" and row["platform_order_code"] == "A1B2C" and row["pickup_eta"]
