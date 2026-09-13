from decimal import Decimal

from django.test import Client

import pytest

from apps.orders.models import Order, OrderItem
from apps.terminals.models import PaymentTerminal
from tests.delivery.conftest import FakeSession  # noqa: F401


@pytest.fixture(autouse=True)
def _tenant_domain(settings):
    settings.MAIN_DOMAIN = "localhost"
    settings.SMS_PROVIDER = "console"
    settings.PUBLIC_API_BASE_URL = "https://api.test"


@pytest.fixture
def terminals(restaurant):
    restaurant.terminals_enabled = True
    restaurant.cash_enabled = True
    restaurant.accepts_remote_orders = True
    restaurant.save()
    from apps.notifications import services as notifications

    cfg = notifications.settings_for(restaurant)
    cfg.guest_sms = True
    cfg.save()
    return restaurant


@pytest.fixture
def manual_terminal(terminals):
    return PaymentTerminal.objects.create(restaurant=terminals, name="Till 1", provider="manual", is_default=True)


@pytest.fixture
def bog_terminal(terminals):
    t = PaymentTerminal(restaurant=terminals, name="BOG QR", provider="bog_link", timeout_seconds=300)
    t.set_credentials({"client_id": "cid", "client_secret": "csec"})
    t.save()
    return t


@pytest.fixture
def tbc_terminal(terminals):
    t = PaymentTerminal(restaurant=terminals, name="TBC QR", provider="tbc_tpay")
    t.set_credentials({"apikey": "ak", "client_id": "cid", "client_secret": "csec"})
    t.save()
    return t


@pytest.fixture
def bridge_terminal(terminals):
    return PaymentTerminal.objects.create(
        restaurant=terminals,
        name="ECR",
        provider="ecr_bridge",
        ecr_protocol="bog",
        connection={"device": "tcp://1.2.3.4:8000"},
    )


def make_order(restaurant, menu_item, total="30.00", status="confirmed"):
    o = Order.objects.create(
        restaurant=restaurant, order_type="dine_in", status=status, source="pos", customer_name="Nino"
    )
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


@pytest.fixture
def paid_order(terminals, menu_item):
    return make_order(terminals, menu_item)


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


@pytest.fixture
def open_shift(terminals, user):
    from apps.payments import services as ledger

    return ledger.open_shift(terminals, by=user, opening_float=Decimal("50"))
