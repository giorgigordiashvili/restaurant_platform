"""
Two restaurants (A = `restaurant`, B = `another_restaurant`) and a third
`rival`, each with a manager. Dashboard clients carry X-Restaurant; admin
clients use the tenant subdomain host.
"""

from django.test import Client

from rest_framework.test import APIClient

import pytest

from apps.staff.models import StaffRole
from apps.tables.models import Table, TableQRCode, TableSection
from apps.venues import services


@pytest.fixture(autouse=True)
def _tenant_domain(settings):
    settings.MAIN_DOMAIN = "localhost"
    settings.FRONTEND_BASE_URL = "https://example.test"


def _manager_for(create_user, create_staff_member, restaurant, email):
    roles = StaffRole.objects.filter(restaurant=restaurant)
    if not roles.exists():
        roles = StaffRole.create_default_roles(restaurant)
    manager_role = next(r for r in roles if r.name == "manager")
    user = create_user(email=email, first_name="Mgr", last_name=restaurant.slug)
    create_staff_member(user=user, restaurant=restaurant, role=manager_role)
    return user


def _api(user, restaurant):
    client = APIClient()
    client.force_authenticate(user)
    client.credentials(HTTP_X_RESTAURANT=restaurant.slug)
    return client


def _admin(user, restaurant):
    client = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
    client.force_login(user)
    return client


@pytest.fixture
def a(restaurant):
    return restaurant


@pytest.fixture
def b(another_restaurant):
    return another_restaurant


@pytest.fixture
def rival(create_restaurant, create_user):
    owner = create_user(email="rival-owner@example.com", first_name="R", last_name="O")
    return create_restaurant(owner=owner, name="Rival Bistro", slug="rival-bistro")


@pytest.fixture
def a_manager(create_user, create_staff_member, a):
    return _manager_for(create_user, create_staff_member, a, "a-manager@example.com")


@pytest.fixture
def b_manager(create_user, create_staff_member, b):
    return _manager_for(create_user, create_staff_member, b, "b-manager@example.com")


@pytest.fixture
def rival_manager(create_user, create_staff_member, rival):
    return _manager_for(create_user, create_staff_member, rival, "rival-manager@example.com")


@pytest.fixture
def a_waiter(create_user, create_staff_member, a):
    roles = StaffRole.objects.filter(restaurant=a) or StaffRole.create_default_roles(a)
    waiter = next(r for r in roles if r.name == "waiter")
    user = create_user(email="a-waiter@example.com", first_name="W", last_name="A")
    create_staff_member(user=user, restaurant=a, role=waiter)
    return user


@pytest.fixture
def a_api(a_manager, a):
    return _api(a_manager, a)


@pytest.fixture
def b_api(b_manager, b):
    return _api(b_manager, b)


@pytest.fixture
def rival_api(rival_manager, rival):
    return _api(rival_manager, rival)


@pytest.fixture
def a_admin(a_manager, a):
    return _admin(a_manager, a)


@pytest.fixture
def b_admin(b_manager, b):
    return _admin(b_manager, b)


def make_layout(restaurant, numbers, sections=("Hall",)):
    """Sections + numbered tables (each with a QR code) for a restaurant."""
    secs = {name: TableSection.objects.create(restaurant=restaurant, name=name) for name in sections}
    first = next(iter(secs.values())) if secs else None
    tables = {}
    for n in numbers:
        t = Table.objects.create(restaurant=restaurant, number=str(n), capacity=4, section=first)
        TableQRCode.objects.create(table=t)
        tables[str(n)] = t
    return secs, tables


@pytest.fixture
def a_layout(a):
    return make_layout(a, [1, 2, 3], sections=("Hall", "Terrace"))


@pytest.fixture
def b_layout(b):
    # B already has its own "2" (same physical table, adopted on accept) and a private "9".
    return make_layout(b, [2, 9], sections=("Hall",))


@pytest.fixture
def pending_request(a, b, a_manager, a_layout, b_layout):
    return services.send_share_request(a, b.slug, a_manager, venue_name="Food Hall", message="Let's share")


@pytest.fixture
def venue_pair(pending_request, b, b_manager):
    """A -> B accepted by B with layout 'theirs' (A's tables seed the venue)."""
    venue, summary = services.accept_share_request(pending_request.pk, b, b_manager, layout=services.LAYOUT_THEIRS)
    return venue
