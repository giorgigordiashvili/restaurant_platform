"""Tenant admin pages: settings (hours, rules, courier keys, pause), zones with map, couriers, deliveries, domains."""

from decimal import Decimal

import pytest

from apps.delivery.models import RestaurantDeliveryPlatform
from apps.ordering import dispatch, services
from apps.ordering.models import DeliveryZone, RestaurantDomain
from apps.tenants.models import RestaurantHours

P = "/tenant-admin/ordering/"


@pytest.mark.django_db
class TestSettingsPage:
    def test_render_and_save(self, ordering, owner_admin):
        html = owner_admin.get(P + "onlineorderingsettingspage/").content.decode()
        assert "Opening hours" in html and "Wolt Drive" in html and 'data-testid="hours-form"' in html
        res = owner_admin.post(
            P + "onlineorderingsettingspage/save/",
            {
                "pickup_enabled": "1",
                "delivery_enabled": "1",
                "asap_enabled": "1",
                "scheduling_enabled": "1",
                "lead_minutes": "35",
                "min_order_delivery": "12.5",
                "courier_provider": "wolt_drive",
                "auto_request_courier_on": "confirmed",
            },
        )
        assert res.status_code == 302
        ordering._ordering_settings_cache = None
        cfg = services.settings_for(ordering)
        assert (
            cfg.lead_minutes == 35
            and cfg.min_order_delivery == Decimal("12.50")
            and cfg.courier_provider == "wolt_drive"
        )
        assert cfg.auto_request_courier_on == "confirmed"

    def test_hours_save_with_second_service(self, ordering, owner_admin):
        data = {}
        for d in range(7):
            data[f"open_{d}"] = "11:00"
            data[f"close_{d}"] = "15:00"
            data[f"open2_{d}"] = "18:00"
            data[f"close2_{d}"] = "01:00"
        data["closed_6"] = "1"
        assert owner_admin.post(P + "onlineorderingsettingspage/hours/", data).status_code == 302
        mon = RestaurantHours.objects.get(restaurant=ordering, day_of_week=0)
        assert mon.open_time_2.hour == 18 and mon.close_time_2.hour == 1 and not mon.is_closed
        assert RestaurantHours.objects.get(restaurant=ordering, day_of_week=6).is_closed

    def test_courier_keys_and_pause(self, ordering, owner_admin):
        res = owner_admin.post(
            P + "onlineorderingsettingspage/courier/wolt_drive/save/",
            {"is_enabled": "1", "sandbox": "1", "store_external_id": "venue-1", "api_key": "k", "client_secret": "s"},
        )
        assert res.status_code == 302
        link = RestaurantDeliveryPlatform.objects.get(restaurant=ordering, platform="wolt_drive")
        assert link.is_enabled and link.get_credentials()["api_key"] == "k"
        assert (
            owner_admin.post(P + "onlineorderingsettingspage/pause/", {"minutes": "20", "reason": "Busy"}).status_code
            == 302
        )
        ordering._ordering_settings_cache = None
        assert services.is_paused(services.settings_for(ordering))
        owner_admin.post(P + "onlineorderingsettingspage/resume/")
        ordering._ordering_settings_cache = None
        assert not services.is_paused(services.settings_for(ordering))

    def test_module_off_hides_pages(self, restaurant, owner_admin):
        assert owner_admin.get(P + "onlineorderingsettingspage/").status_code == 403
        assert "Online ordering" not in owner_admin.get("/tenant-admin/").content.decode()


@pytest.mark.django_db
class TestZonesCouriersDeliveriesDomains:
    def test_zone_pages(self, ordering, zone, owner_admin):
        html = owner_admin.get(P + "deliveryzone/").content.decode()
        assert 'id="zones-map"' in html and "Inner ring" in html and "leaflet.js" in html
        html = owner_admin.get(P + "deliveryzone/add/").content.decode()
        assert 'id="zone-map"' in html and "leaflet.draw.js" in html
        res = owner_admin.post(
            P + "deliveryzone/add/",
            {
                "name": "Poly",
                "kind": "polygon",
                "radius_km": "3",
                "polygon": "[[41.7,44.8],[41.7,44.9],[41.8,44.9]]",
                "fee": "4",
                "min_order": "0",
                "eta_minutes": "50",
                "sort": "1",
                "color": "",
                "is_active": "on",
            },
        )
        assert res.status_code == 302, res.content.decode()[:500]
        assert DeliveryZone.objects.get(restaurant=ordering, name="Poly").polygon[0] == [41.7, 44.8]
        res = owner_admin.post(
            P + "deliveryzone/add/",
            {
                "name": "Bad",
                "kind": "polygon",
                "radius_km": "3",
                "polygon": "[]",
                "fee": "4",
                "min_order": "0",
                "eta_minutes": "50",
                "sort": "1",
                "color": "",
            },
        )
        assert res.status_code == 200 and "at least three points" in res.content.decode()

    def test_deliveries_and_couriers(self, ordering, delivery_order, courier, owner_admin, user):
        d = dispatch.request_courier(delivery_order, by=user)
        html = owner_admin.get(P + "delivery/").content.decode()
        assert delivery_order.order_number in html
        html = owner_admin.get(P + f"delivery/{d.pk}/assign/").content.decode()
        assert "Gio" in html
        assert owner_admin.post(P + f"delivery/{d.pk}/assign/", {"courier_id": str(courier.pk)}).status_code == 302
        d.refresh_from_db()
        assert d.status == "assigned"
        assert owner_admin.get(P + f"delivery/{d.pk}/cancel/").status_code == 302
        d.refresh_from_db()
        assert d.status == "cancelled"
        assert "Gio" in owner_admin.get(P + "courier/").content.decode()

    def test_domains_page(self, ordering, owner_admin, monkeypatch, settings):
        settings.PUBLIC_IP = "1.2.3.4"
        monkeypatch.setattr("apps.ordering.domains.resolves_to_us", lambda d: (True, ""))
        html = owner_admin.get(P + "restaurantdomain/").content.decode()
        assert "sites.aimenu.ge" in html and "1.2.3.4" in html
        res = owner_admin.post(P + "restaurantdomain/add/", {"domain": "order.example.ge", "is_primary": "on"})
        assert res.status_code == 302
        row = RestaurantDomain.objects.get(domain="order.example.ge")
        assert row.is_verified
        assert owner_admin.get(P + f"restaurantdomain/{row.pk}/verify/").status_code == 302
