"""Role editor: checkbox grid, custom roles, member editing."""

from django.test import Client

import pytest

from apps.staff.models import StaffMember, StaffRole

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _tenant_domain(settings):
    settings.MAIN_DOMAIN = "localhost"


@pytest.fixture
def owner_admin(user, restaurant, staff_roles, create_staff_member):
    create_staff_member(user=user, restaurant=restaurant, role=next(r for r in staff_roles if r.name == "owner"))
    client = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
    client.force_login(user)
    return client


def test_role_form_renders_grid_and_round_trips(owner_admin, restaurant, staff_roles):
    waiter = next(r for r in staff_roles if r.name == "waiter")
    url = f"/tenant-admin/staff/staffrole/{waiter.pk}/change/"
    html = owner_admin.get(url).content.decode()
    assert 'data-testid="permission-matrix"' in html
    assert 'name="permissions__menu__read" value="1" checked' in html
    assert "permissions__warehouse__read" not in html  # module off
    resp = owner_admin.post(
        url,
        {
            "display_name": "",
            "description": "",
            "permissions__menu__read": "1",
            "permissions__orders__read": "1",
            "permissions__orders__update": "1",
            "permissions__reservations__create": "1",
        },
    )
    assert resp.status_code == 302, resp.content[:500]
    waiter.refresh_from_db()
    assert waiter.permissions == {
        "menu": ["read"],
        "orders": ["read", "update"],
        "reservations": ["create"],
        "warehouse_logs": ["create", "read", "update"],  # hidden module: kept as it was
        "timekeeping": ["create", "read"],  # hidden module: kept as it was
    }
    assert waiter.name == "waiter" and waiter.is_system_role


def test_hidden_module_permissions_survive_a_save(owner_admin, restaurant, staff_roles):
    kitchen = next(r for r in staff_roles if r.name == "kitchen")
    assert "warehouse" in kitchen.permissions  # default, module off
    resp = owner_admin.post(
        f"/tenant-admin/staff/staffrole/{kitchen.pk}/change/",
        {"display_name": "", "description": "", "permissions__menu__read": "1"},
    )
    assert resp.status_code == 302
    kitchen.refresh_from_db()
    assert kitchen.permissions["warehouse"] == ["read"]
    assert kitchen.permissions["menu"] == ["read"] and "orders" not in kitchen.permissions


def test_custom_roles(owner_admin, restaurant, staff_roles):
    add = "/tenant-admin/staff/staffrole/add/"
    html = owner_admin.get(add).content.decode()
    assert "Custom role" in html
    for name in ("Head chef", "Host"):
        resp = owner_admin.post(
            add, {"name": "custom", "display_name": name, "description": "", "permissions__menu__read": "1"}
        )
        assert resp.status_code == 302, resp.content[:500]
    roles = StaffRole.objects.filter(restaurant=restaurant, name="custom")
    assert {r.display_name for r in roles} == {"Head chef", "Host"}
    assert all(not r.is_system_role for r in roles)
    # duplicate display name rejected
    resp = owner_admin.post(add, {"name": "custom", "display_name": "Host", "description": ""})
    assert resp.status_code == 200 and "already exists" in resp.content.decode().lower()
    # blank name rejected
    resp = owner_admin.post(add, {"name": "custom", "display_name": "", "description": ""})
    assert resp.status_code == 200
    # system role names are read-only and undeletable
    owner = next(r for r in staff_roles if r.name == "owner")
    assert owner_admin.get(f"/tenant-admin/staff/staffrole/{owner.pk}/delete/").status_code == 403


def test_member_admin(owner_admin, restaurant, waiter_staff, staff_roles):
    assert owner_admin.get("/tenant-admin/staff/staffmember/add/").status_code == 403
    url = f"/tenant-admin/staff/staffmember/{waiter_staff.pk}/change/"
    html = owner_admin.get(url).content.decode()
    assert "permissions_override__menu__read" in html
    kitchen = next(r for r in staff_roles if r.name == "kitchen")
    resp = owner_admin.post(
        url,
        {"role": str(kitchen.pk), "is_active": "on", "notes": "", "permissions_override__reservations__read": "1"},
    )
    assert resp.status_code == 302, resp.content[:500]
    waiter_staff.refresh_from_db()
    assert waiter_staff.role == kitchen
    assert waiter_staff.permissions_override == {"reservations": ["read"]}
    assert waiter_staff.get_effective_permissions()["reservations"] == ["read"]


def test_owner_cannot_be_deactivated(owner_admin, restaurant, user):
    me = StaffMember.objects.get(user=user, restaurant=restaurant)
    resp = owner_admin.post(f"/tenant-admin/staff/staffmember/{me.pk}/change/", {"role": str(me.role_id), "notes": ""})
    assert resp.status_code == 302
    me.refresh_from_db()
    assert me.is_active is True
