"""Fixtures: a restaurant with online ordering on, hours, a pin on the map, one zone, a delivery order, fake courier HTTP."""

from datetime import time
from decimal import Decimal

from django.test import Client

import pytest

from apps.ordering import services
from apps.ordering.models import Courier, DeliveryZone
from apps.orders.models import Order, OrderItem
from apps.tenants.models import RestaurantHours
from tests.delivery.conftest import FakeResponse, FakeSession  # noqa: F401

TBILISI = (Decimal("41.715100"), Decimal("44.827100"))
NEAR = (41.7200, 44.8300)  # ~0.6 km away
FAR = (41.8000, 44.9500)  # ~14 km away


@pytest.fixture(autouse=True)
def _tenant_domain(settings):
    settings.MAIN_DOMAIN = "localhost"
    settings.SMS_PROVIDER = "console"


@pytest.fixture
def ordering(restaurant):
    restaurant.online_ordering_enabled = True
    restaurant.accepts_remote_orders = True
    restaurant.accepts_takeaway = True
    restaurant.cash_enabled = False
    restaurant.latitude, restaurant.longitude = TBILISI
    restaurant.timezone = "Asia/Tbilisi"
    restaurant.save()
    for day in range(7):
        RestaurantHours.objects.update_or_create(
            restaurant=restaurant,
            day_of_week=day,
            defaults={"open_time": time(10, 0), "close_time": time(22, 0), "is_closed": False},
        )
    cfg = services.settings_for(restaurant)
    cfg.delivery_enabled = True
    cfg.pickup_enabled = True
    cfg.lead_minutes = 20
    cfg.delivery_extra_minutes = 10
    cfg.cutoff_minutes_before_close = 30
    cfg.save()
    from apps.notifications import services as notifications

    ncfg = notifications.settings_for(restaurant)
    ncfg.guest_sms = True
    ncfg.save()
    restaurant._hours_cache = None
    return restaurant


@pytest.fixture
def zone(ordering):
    return DeliveryZone.objects.create(
        restaurant=ordering,
        name="Inner ring",
        kind="radius",
        radius_km=Decimal("3"),
        fee=Decimal("5.00"),
        min_order=Decimal("15.00"),
        eta_minutes=40,
        sort=0,
    )


@pytest.fixture
def owner_admin(user, restaurant, staff_roles, create_staff_member):
    create_staff_member(user=user, restaurant=restaurant, role=next(r for r in staff_roles if r.name == "owner"))
    client = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
    client.force_login(user)
    return client


@pytest.fixture
def owner_api(authenticated_owner_client, restaurant):
    authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
    return authenticated_owner_client


@pytest.fixture
def courier(ordering):
    return Courier.objects.create(restaurant=ordering, name="Gio", phone="+995555000001", vehicle="scooter")


def make_delivery_order(restaurant, menu_item, *, status="pending", lat=NEAR[0], lng=NEAR[1], scheduled_for=None):
    order = Order.objects.create(
        restaurant=restaurant,
        order_type="delivery",
        status=status,
        source="web",
        customer_name="Nino",
        customer_phone="+995555123456",
        delivery_address="Rustaveli 12",
        address_json={"street": "Rustaveli", "building": "12", "entrance": "2", "floor": "3", "apartment": "7"},
        delivery_lat=Decimal(str(lat)),
        delivery_lng=Decimal(str(lng)),
        delivery_fee=Decimal("5.00"),
        scheduled_for=scheduled_for,
    )
    OrderItem.objects.create(
        order=order,
        menu_item=menu_item,
        item_name="Khachapuri",
        unit_price=Decimal("20.00"),
        quantity=2,
        total_price=Decimal("40.00"),
    )
    order.calculate_totals()
    return order


@pytest.fixture
def delivery_order(ordering, menu_item, zone):
    return make_delivery_order(ordering, menu_item)


@pytest.fixture
def courier_link(ordering):
    """Both courier platforms enabled with keys (wolt_drive first)."""
    from apps.delivery.models import RestaurantDeliveryPlatform

    wolt = RestaurantDeliveryPlatform(
        restaurant=ordering, platform="wolt_drive", is_enabled=True, store_external_id="venue-9", sandbox=True
    )
    wolt.set_credentials({"api_key": "wd-key", "client_secret": "wd-secret", "merchant_id": "m-1"})
    wolt.save()
    glovo = RestaurantDeliveryPlatform(
        restaurant=ordering, platform="glovo_odr", is_enabled=True, store_external_id="vendor-7", sandbox=True
    )
    glovo.set_credentials({"client_id": "cid", "client_secret": "csec", "callback_secret": "cb-secret"})
    glovo.save()
    return {"wolt_drive": wolt, "glovo_odr": glovo}


@pytest.fixture
def fake_courier(monkeypatch):
    """One FakeSession behind both courier clients; the cache is cleared so Glovo fetches a token."""
    from django.core.cache import cache

    from apps.delivery.courier import glovo_odr, wolt_drive

    cache.clear()
    session = FakeSession()

    def _wolt(link, *, session=None):
        return wolt_drive.WoltDriveClient(wolt_drive.resolve_config(link), session=session or _wolt.session)

    def _glovo(link, *, session=None):
        return glovo_odr.GlovoOdrClient(glovo_odr.resolve_config(link), link=link, session=session or _glovo.session)

    _wolt.session = session
    _glovo.session = session
    monkeypatch.setattr("apps.delivery.courier.wolt_drive.build_client", _wolt)
    monkeypatch.setattr("apps.delivery.courier.glovo_odr.build_client", _glovo)
    return session
