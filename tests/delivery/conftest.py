"""
Hand-written fixtures modelled on the public Glovo Partners / Wolt developer
docs. NOT recorded from live traffic; see apps/delivery/glovo/__init__.py
"VERIFY ON STAGE" and apps/delivery/wolt/__init__.py "VERIFY ON DEV".
"""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "glovo"
WOLT_FIXTURES = Path(__file__).parent / "fixtures" / "wolt"


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        if isinstance(body, bytes):  # binary (image) response
            self.text = ""
            self.content = body
            self.headers = {"Content-Type": "image/jpeg"}
        else:
            self.text = json.dumps(body) if body is not None else ""
            self.content = self.text.encode()
            self.headers = {"Content-Type": "application/json"}

    def json(self):
        if self._body is None or isinstance(self._body, bytes):
            raise ValueError("no body")
        return self._body

    @property
    def ok(self):
        return self.status_code < 400


class FakeSession:
    """Replays (status, body) responses in order and records every call."""

    def __init__(self, *responses):
        self.calls = []
        self.queue = list(responses)

    def request(self, method, url, json=None, headers=None, timeout=None, data=None, auth=None, **kwargs):
        self.calls.append(
            {"method": method, "url": url, "json": json, "headers": headers, "data": data, "auth": auth, **kwargs}
        )
        status, body = self.queue.pop(0) if self.queue else (202, {"transactionId": "tx-auto"})
        return FakeResponse(status, body)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


@pytest.fixture
def glovo_fixture():
    def load(name):
        return json.loads((FIXTURES / f"{name}.json").read_text())

    return load


@pytest.fixture
def delivery_restaurant(restaurant):
    restaurant.delivery_enabled = True
    restaurant.accepts_remote_orders = True
    restaurant.cash_enabled = False
    restaurant.save(update_fields=["delivery_enabled", "accepts_remote_orders", "cash_enabled"])
    return restaurant


@pytest.fixture
def glovo_link(delivery_restaurant):
    from apps.delivery.models import RestaurantDeliveryPlatform

    link = RestaurantDeliveryPlatform(
        restaurant=delivery_restaurant,
        platform="glovo",
        is_enabled=True,
        store_external_id="store-1",
        webhook_token="tok",
        menu_token="mt",
        auto_accept=True,
        sandbox=True,
    )
    link.set_credentials({"api_token": "x-token"})
    link.save()
    return link


@pytest.fixture
def fake_glovo(monkeypatch):
    """Inject a FakeSession into every GlovoClient built through build_client."""
    from apps.delivery.config import resolve_glovo_config
    from apps.delivery.glovo import client as client_module

    session = FakeSession()

    def _build(link, session=None):
        return client_module.GlovoClient(resolve_glovo_config(link), session=session or _build.session)

    _build.session = session
    monkeypatch.setattr("apps.delivery.glovo.client.build_client", _build)
    monkeypatch.setattr("apps.delivery.glovo.adapter.build_client", _build)
    return session


@pytest.fixture
def wolt_fixture():
    def load(name):
        return json.loads((WOLT_FIXTURES / f"{name}.json").read_text())

    return load


@pytest.fixture
def wolt_link(delivery_restaurant):
    from apps.delivery.models import RestaurantDeliveryPlatform

    link = RestaurantDeliveryPlatform(
        restaurant=delivery_restaurant,
        platform="wolt",
        is_enabled=True,
        store_external_id="venue-1",
        webhook_token="wolt-secret",
        auto_accept=True,
        sandbox=True,
    )
    link.set_credentials({"client_id": "cid", "client_secret": "csec", "refresh_token": "rt-1"})
    link.save()
    return link


@pytest.fixture
def fake_wolt(monkeypatch):
    """Inject a FakeSession into every WoltClient built through build_client; cache cleared so tokens refresh."""
    from django.core.cache import cache

    from apps.delivery.config import resolve_wolt_config
    from apps.delivery.wolt import client as client_module

    cache.clear()
    session = FakeSession()

    def _build(link, session=None):
        return client_module.WoltClient(resolve_wolt_config(link), link=link, session=session or _build.session)

    _build.session = session
    monkeypatch.setattr("apps.delivery.wolt.client.build_client", _build)
    monkeypatch.setattr("apps.delivery.wolt.adapter.build_client", _build)
    monkeypatch.setattr("apps.delivery.menu_import.new_session", lambda: _build.session)
    return session
