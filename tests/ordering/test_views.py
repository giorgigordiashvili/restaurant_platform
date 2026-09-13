"""Public config / slots / quote, dashboard settings, zones, couriers, deliveries, domains, module gating."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from rest_framework.test import APIClient

import pytest

from apps.ordering import dispatch
from apps.ordering.models import DeliveryZone, RestaurantDomain
from tests.ordering.conftest import FAR, NEAR

TBI = ZoneInfo("Asia/Tbilisi")
D = "/api/v1/dashboard/ordering/"


@pytest.mark.django_db
class TestPublic:
    def test_config_slots_quote(self, ordering, zone, monkeypatch):
        monkeypatch.setattr("apps.ordering.services.timezone.now", lambda: datetime(2026, 9, 14, 12, 0, tzinfo=TBI))
        cfg = APIClient().get(f"/api/v1/ordering/{ordering.slug}/config/").json()["data"]
        assert (
            cfg["enabled"]
            and cfg["pickup"]
            and cfg["delivery"]
            and cfg["open_now"]
            and cfg["zones"][0]["fee"] == "5.00"
        )
        assert cfg["restaurant_location"]["lat"] == 41.7151
        slots = APIClient().get(f"/api/v1/ordering/{ordering.slug}/slots/?kind=takeaway&date=2026-09-14").json()["data"]
        assert slots["slots"][0]["label"] == "12:30"
        assert APIClient().get(f"/api/v1/ordering/{ordering.slug}/slots/?kind=x").status_code == 400
        q = APIClient().post(
            f"/api/v1/ordering/{ordering.slug}/delivery-quote/",
            {"lat": NEAR[0], "lng": NEAR[1], "subtotal": "20"},
            format="json",
        )
        assert q.status_code == 200 and q.json()["data"]["fee"] == "5.00" and q.json()["data"]["eta_minutes"] == 40
        q = APIClient().post(
            f"/api/v1/ordering/{ordering.slug}/delivery-quote/", {"lat": FAR[0], "lng": FAR[1]}, format="json"
        )
        assert q.status_code == 400 and q.json()["error"]["code"] == "out_of_zone"

    def test_config_when_module_off(self, restaurant):
        cfg = APIClient().get(f"/api/v1/ordering/{restaurant.slug}/config/").json()["data"]
        assert cfg["enabled"] is False and cfg["delivery"] is False

    def test_by_domain_and_check(self, ordering):
        RestaurantDomain.objects.create(restaurant=ordering, domain="order.example.ge")
        res = APIClient().get("/api/v1/ordering/by-domain/?host=ORDER.example.ge:443")
        assert res.status_code == 200 and res.json()["data"]["slug"] == ordering.slug
        assert APIClient().get("/api/v1/ordering/by-domain/?host=nope.ge").status_code == 404
        assert APIClient().get("/api/v1/ordering/domains/check/?domain=order.example.ge").status_code == 200
        assert APIClient().get("/api/v1/ordering/domains/check/?domain=evil.ge").status_code == 404


@pytest.mark.django_db
class TestDashboard:
    def test_settings_and_pause(self, ordering, owner_api):
        res = owner_api.get(D + "settings/")
        assert res.status_code == 200 and res.data["lead_minutes"] == 20
        res = owner_api.patch(D + "settings/", {"lead_minutes": 25, "courier_provider": "own"}, format="json")
        assert res.status_code == 200 and res.data["lead_minutes"] == 25
        res = owner_api.post(D + "pause/", {"minutes": 10, "reason": "Busy"}, format="json")
        assert res.status_code == 200 and res.data["paused_until"]
        assert owner_api.get(D + "summary/").data["paused"] is True
        assert owner_api.delete(D + "pause/").data["paused_until"] is None

    def test_zones_crud(self, ordering, owner_api):
        res = owner_api.post(
            D + "zones/",
            {"name": "Poly", "kind": "polygon", "polygon": [[41.7, 44.8], [41.7, 44.9]], "fee": "3"},
            format="json",
        )
        assert res.status_code == 400
        res = owner_api.post(
            D + "zones/",
            {"name": "Poly", "kind": "polygon", "polygon": [[41.7, 44.8], [41.7, 44.9], [41.8, 44.9]], "fee": "3"},
            format="json",
        )
        assert res.status_code == 201, res.content
        zid = res.data["id"]
        assert owner_api.get(D + "zones/").data[0]["name"] == "Poly"
        assert owner_api.patch(D + f"zones/{zid}/", {"fee": "4.50"}, format="json").data["fee"] == "4.50"
        assert owner_api.delete(D + f"zones/{zid}/").status_code == 204
        assert not DeliveryZone.objects.filter(pk=zid).exists()

    def test_couriers_and_deliveries(self, ordering, owner_api, delivery_order, user):
        res = owner_api.post(D + "couriers/", {"name": "Gio", "phone": "+995555000001"}, format="json")
        assert res.status_code == 201
        cid = res.data["id"]
        assert owner_api.get(D + "couriers/me/").status_code == 404
        res = owner_api.post(D + f"orders/{delivery_order.pk}/delivery/request/", {}, format="json")
        assert res.status_code == 201 and res.data["status"] == "requested" and res.data["provider"] == "own"
        res = owner_api.post(D + f"orders/{delivery_order.pk}/delivery/assign/", {"courier_id": cid}, format="json")
        assert res.status_code == 200 and res.data["courier_name"] == "Gio"
        rows = owner_api.get(D + "deliveries/?status=open").data
        assert len(rows) == 1 and rows[0]["address_json"]["building"] == "12" and rows[0]["lat"] == "41.720000"
        res = owner_api.post(D + f"orders/{delivery_order.pk}/delivery/update/", {"status": "picked_up"}, format="json")
        assert res.status_code == 200 and res.data["status"] == "picked_up"
        res = owner_api.post(D + f"orders/{delivery_order.pk}/delivery/update/", {"status": "assigned"}, format="json")
        assert res.status_code == 409
        assert owner_api.get(D + f"orders/{delivery_order.pk}/delivery/").data["status"] == "picked_up"
        res = owner_api.post(D + f"orders/{delivery_order.pk}/delivery/cancel/", {"reason": "x"}, format="json")
        assert res.data["status"] == "cancelled"

    def test_rider_can_move_own_delivery_only(
        self, ordering, delivery_order, waiter_staff, authenticated_waiter_client, user
    ):
        from apps.ordering.models import Courier

        waiter_staff.role.permissions = {**waiter_staff.role.permissions, "orders": ["read"]}
        waiter_staff.role.save()
        me = Courier.objects.create(restaurant=ordering, name="W", staff=waiter_staff)
        other = Courier.objects.create(restaurant=ordering, name="O")
        authenticated_waiter_client.defaults["HTTP_X_RESTAURANT"] = ordering.slug
        d = dispatch.request_courier(delivery_order, by=user)
        dispatch.assign_own(d, other, by=user)
        res = authenticated_waiter_client.post(
            D + f"orders/{delivery_order.pk}/delivery/update/", {"status": "picked_up"}, format="json"
        )
        assert res.status_code == 403
        dispatch.assign_own(d, me, by=user)
        assert authenticated_waiter_client.get(D + "couriers/me/").data["name"] == "W"
        assert (
            authenticated_waiter_client.get(D + "deliveries/?mine=1").data[0]["order_number"]
            == delivery_order.order_number
        )
        res = authenticated_waiter_client.post(
            D + f"orders/{delivery_order.pk}/delivery/update/", {"status": "picked_up"}, format="json"
        )
        assert res.status_code == 200

    def test_domains(self, ordering, owner_api, monkeypatch):
        monkeypatch.setattr(
            "apps.ordering.domains.resolves_to_us",
            lambda d: (d == "order.good.ge", "" if d == "order.good.ge" else "nope"),
        )
        assert owner_api.post(D + "domains/", {"domain": "not a domain"}, format="json").status_code == 400
        assert owner_api.post(D + "domains/", {"domain": "x.aimenu.ge"}, format="json").status_code == 400
        res = owner_api.post(D + "domains/", {"domain": "Order.Good.ge"}, format="json")
        assert res.status_code == 201 and res.data["is_verified"] is True and res.data["domain"] == "order.good.ge"
        res = owner_api.post(D + "domains/", {"domain": "order.bad.ge"}, format="json")
        assert res.status_code == 201 and res.data["is_verified"] is False and res.data["error"] == "nope"
        assert owner_api.post(D + f"domains/{res.data['id']}/verify/").data["is_verified"] is False
        assert len(owner_api.get(D + "domains/").data) == 2

    def test_module_off_blocks(self, restaurant, owner_api):
        res = owner_api.get(D + "settings/")
        assert res.status_code == 403 and res.data["code"] == "module_disabled"
