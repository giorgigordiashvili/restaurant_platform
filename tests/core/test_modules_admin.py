"""Tenant admin adapts to modules: sidebar, Modules page, dashboard index, gated pages."""

from django.test import Client

import pytest

from apps.core import modules
from apps.staff.models import StaffRole

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _tenant_domain(settings):
    settings.MAIN_DOMAIN = "localhost"


def _admin(user, restaurant):
    client = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
    client.force_login(user)
    return client


@pytest.fixture
def owner_admin(user, restaurant, staff_roles, create_staff_member):
    create_staff_member(user=user, restaurant=restaurant, role=next(r for r in staff_roles if r.name == "owner"))
    return _admin(user, restaurant)


@pytest.fixture
def waiter_admin(waiter_staff, restaurant):
    waiter_staff.user.is_staff = True
    waiter_staff.user.save()
    return _admin(waiter_staff.user, restaurant)


MODULES_PAGE = "/tenant-admin/tenants/restaurantmodules/"


def test_sidebar_follows_modules(owner_admin, restaurant, user):
    html = owner_admin.get("/tenant-admin/").content.decode()
    for title in (
        "Menu",
        "Ordering",
        "Tables &amp; QR",
        "Reservations",
        "Loyalty",
        "Reviews",
        "Staff",
        "Settings",
        "Modules",
    ):
        assert title in html, title
    assert "Warehouse" not in html
    modules.set_module(restaurant, "reservations", False, by=user)
    modules.set_module(restaurant, "warehouse", True, by=user)
    html = owner_admin.get("/tenant-admin/").content.decode()
    assert "Reservations" not in html
    assert "Warehouse" in html


def test_sidebar_respects_roles(waiter_admin):
    html = waiter_admin.get("/tenant-admin/").content.decode()
    assert 'data-testid="tenant-dashboard"' in html
    assert "/tenant-admin/staff/staffmember/" not in html  # waiter has no staff:read
    assert "/tenant-admin/tenants/restaurantmodules/" not in html
    assert "/tenant-admin/menu/menuitem/" in html


@pytest.mark.parametrize(
    "code,url",
    [
        ("ordering", "/tenant-admin/orders/order/"),
        ("tables", "/tenant-admin/tables/table/"),
        ("reservations", "/tenant-admin/reservations/reservation/"),
        ("loyalty", "/tenant-admin/loyalty/loyaltyprogram/"),
        ("reviews", "/tenant-admin/reviews/review/"),
    ],
)
def test_module_pages_disappear_when_off(owner_admin, restaurant, user, code, url):
    assert owner_admin.get(url).status_code == 200
    if code == "ordering":
        modules.set_module(restaurant, "kitchen", False, by=user)
        modules.set_module(restaurant, "cash", False, by=user)
    modules.set_module(restaurant, code, False, by=user)
    assert owner_admin.get(url).status_code == 403


def test_modules_page_toggles(owner_admin, restaurant):
    html = owner_admin.get(MODULES_PAGE).content.decode()
    assert 'data-testid="module-ordering"' in html and 'data-testid="module-payments"' in html
    resp = owner_admin.post(f"{MODULES_PAGE}kitchen/disable/")
    assert resp.status_code == 302
    restaurant.refresh_from_db()
    assert restaurant.kitchen_enabled is False
    # dependency: ordering off while kitchen is on
    owner_admin.post(f"{MODULES_PAGE}kitchen/enable/")
    resp = owner_admin.post(f"{MODULES_PAGE}ordering/disable/", follow=True)
    assert "needs" in resp.content.decode()
    restaurant.refresh_from_db()
    assert restaurant.accepts_remote_orders is True
    # options
    owner_admin.post(f"{MODULES_PAGE}ordering/options/", {})  # checkbox unchecked
    restaurant.refresh_from_db()
    assert restaurant.accepts_takeaway is False
    resp = owner_admin.post(
        f"{MODULES_PAGE}payments/options/", {"accepts_bog_payments": "1", "bog_payout_iban": ""}, follow=True
    )
    assert "IBAN" in resp.content.decode()
    restaurant.refresh_from_db()
    assert restaurant.accepts_bog_payments is False


def test_modules_page_requires_settings_update(waiter_admin, restaurant, manager_staff):
    assert waiter_admin.get(MODULES_PAGE).status_code == 403
    manager_staff.user.is_staff = True
    manager_staff.user.save()
    manager = _admin(manager_staff.user, restaurant)
    assert manager.get(MODULES_PAGE).status_code == 200  # settings: read
    assert manager.post(f"{MODULES_PAGE}kitchen/disable/").status_code == 403
    restaurant.refresh_from_db()
    assert restaurant.kitchen_enabled is True


def test_settings_page_no_longer_carries_flags_and_uses_role_permissions(owner_admin, restaurant, manager_staff):
    html = owner_admin.get(f"/tenant-admin/tenants/restaurant/{restaurant.pk}/change/").content.decode()
    assert 'name="warehouse_enabled"' not in html and 'name="accepts_remote_orders"' not in html
    assert "delivery_platforms-TOTAL_FORMS" not in html  # warehouse off
    # a custom role granted settings:update may edit settings
    role = StaffRole.objects.create(
        restaurant=restaurant, name="custom", display_name="Ops", permissions={"settings": ["read", "update"]}
    )
    manager_staff.role = role
    manager_staff.save()
    manager_staff.user.is_staff = True
    manager_staff.user.save()
    client = _admin(manager_staff.user, restaurant)
    resp = client.get(f"/tenant-admin/tenants/restaurant/{restaurant.pk}/change/")
    assert resp.status_code == 200
    assert 'name="name"' in resp.content.decode()  # editable form, not read-only


def test_dashboard_index_cards(owner_admin, restaurant, user, waiter_admin):
    html = owner_admin.get("/tenant-admin/").content.decode()
    assert 'data-testid="tenant-dashboard"' in html
    assert 'data-testid="card-ordering"' in html and 'data-testid="card-staff"' in html
    assert 'data-testid="card-warehouse"' not in html
    modules.set_module(restaurant, "warehouse", True, by=user)
    assert 'data-testid="card-warehouse"' in owner_admin.get("/tenant-admin/").content.decode()
    waiter_html = waiter_admin.get("/tenant-admin/").content.decode()
    assert 'data-testid="card-staff"' not in waiter_html


def test_reservation_actions(owner_admin, restaurant):
    from django.utils import timezone

    from apps.reservations.models import Reservation

    r = Reservation.objects.create(
        restaurant=restaurant,
        guest_name="Ann",
        guest_phone="+995500000000",
        reservation_date=timezone.localdate(),
        reservation_time="19:00",
        party_size=2,
        status="pending",
    )
    resp = owner_admin.post(
        "/tenant-admin/reservations/reservation/",
        {"action": "confirm_reservations", "_selected_action": [str(r.pk)]},
        follow=True,
    )
    assert resp.status_code == 200
    r.refresh_from_db()
    assert r.status == "confirmed"


def test_platform_admin_keeps_default_sidebar(admin_client):
    assert admin_client.get("/admin/").status_code == 200
