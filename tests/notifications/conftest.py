import pytest

from tests.delivery.conftest import FakeSession


@pytest.fixture
def fake_http(monkeypatch):
    """One FakeSession behind every provider call (Expo push, SMS gateways)."""
    session = FakeSession()
    monkeypatch.setattr("apps.notifications.providers.new_session", lambda: session)
    return session


@pytest.fixture
def owner_member(user, restaurant, staff_roles, create_staff_member):
    return create_staff_member(user=user, restaurant=restaurant, role=next(r for r in staff_roles if r.name == "owner"))
