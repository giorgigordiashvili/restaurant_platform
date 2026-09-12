"""Roles, the permission backfill and the tenant-admin warehouse pages."""

from decimal import Decimal

import pytest

from apps.core.permissions import staff_can
from apps.inventory import services
from apps.inventory.models import (
    EmployeeMeal,
    InventoryAlert,
    RecipeLine,
    RestaurantDeliveryPlatform,
    StockAdjustment,
    StockLot,
    WasteEntry,
)
from apps.staff.models import StaffMember, StaffRole

from .conftest import admin

pytestmark = pytest.mark.django_db


# ── roles & permissions ───────────────────────────────────────────────────


def test_default_roles_include_warehouse_manager(roles):
    assert "warehouse_manager" in roles
    assert roles["warehouse_manager"].permissions["warehouse"] == ["create", "read", "update", "delete"]
    assert roles["kitchen"].permissions["warehouse_logs"] == ["create", "read", "update"]
    assert "warehouse" not in roles["waiter"].permissions


def test_backfill_adds_keys_without_overwriting(wh):
    from importlib import import_module

    from django.apps import apps as django_apps

    migration = import_module("apps.staff.migrations.0002_warehouse")
    # Pre-existing role with a customised permission set and no warehouse keys.
    role = StaffRole.objects.create(
        restaurant=wh, name="kitchen", is_system_role=True, permissions={"menu": ["read"], "orders": ["read"]}
    )
    migration.backfill(django_apps, None)
    role.refresh_from_db()
    assert role.permissions["menu"] == ["read"]  # untouched
    assert role.permissions["warehouse"] == ["read"]
    assert StaffRole.objects.filter(restaurant=wh, name="warehouse_manager", is_system_role=True).exists()
    migration.backfill(django_apps, None)  # idempotent
    assert StaffRole.objects.filter(restaurant=wh, name="warehouse_manager").count() == 1


def test_permission_override_is_honoured(wh, waiter):
    class Req:
        pass

    req = Req()
    req.restaurant, req.user = wh, waiter
    assert staff_can(req, "warehouse", "read") is False
    member = StaffMember.objects.get(user=waiter, restaurant=wh)
    member.permissions_override = {"warehouse": ["read"]}
    member.save()
    assert staff_can(req, "warehouse", "read") is True


# ── tenant admin ──────────────────────────────────────────────────────────


def test_sidebar_hides_warehouse_until_enabled(manager, wh):
    client = admin(manager, wh)
    assert "Warehouse overview" in client.get("/tenant-admin/").content.decode()
    wh.warehouse_enabled = False
    wh.save()
    assert "Warehouse overview" not in client.get("/tenant-admin/").content.decode()
    assert client.get("/tenant-admin/inventory/warehouseoverview/").status_code == 403


def test_overview_renders_for_warehouse_manager(warehouse_manager, wh, pizza, flour, lots, order_factory):
    RestaurantDeliveryPlatform.objects.create(restaurant=wh, platform="glovo")
    services.reserve_for_order(order_factory([(pizza, 7, [])]))
    html = admin(warehouse_manager, wh).get("/tenant-admin/inventory/warehouseoverview/").content.decode()
    assert 'data-testid="warehouse-overview"' in html
    assert 'data-testid="alert-out_of_stock"' in html
    assert "Disable on Glovo" in html
    assert "Flour" in html  # buy list / low stock
    assert 'data-testid="quick-actions"' in html


def test_kitchen_sees_logs_but_not_stock(kitchen, wh, flour):
    client = admin(kitchen, wh)
    assert client.get("/tenant-admin/inventory/wasteentry/add/").status_code == 200
    assert client.get("/tenant-admin/inventory/employeemeal/add/").status_code == 200
    assert client.get("/tenant-admin/inventory/stocklot/add/").status_code == 403
    assert client.get("/tenant-admin/inventory/stockitem/").status_code == 200  # read only
    assert client.get("/tenant-admin/inventory/stockitem/add/").status_code == 403


def test_waiter_cannot_open_stock_pages(waiter, wh, flour):
    client = admin(waiter, wh)
    assert client.get("/tenant-admin/inventory/stockitem/").status_code == 403
    assert client.get("/tenant-admin/inventory/wasteentry/add/").status_code == 200


def test_receive_form_creates_lot_through_service(manager_admin, flour, units):
    resp = manager_admin.post(
        "/tenant-admin/inventory/stocklot/add/",
        {
            "stock_item": str(flour.pk),
            "quantity": "2",
            "unit": str(units["kg"].pk),
            "unit_cost_entered": "1.5",
            "total_cost_entered": "",
            "expiry_date": "2030-01-01",
            "supplier_name": "Mill",
            "reference": "INV-1",
            "notes": "",
        },
    )
    assert resp.status_code == 302, resp.content[:500]
    lot = StockLot.objects.get(stock_item=flour)
    assert lot.received_qty == Decimal("2000") and lot.unit_cost == Decimal("0.0015")
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("2000")
    # unit mismatch is a form error, not a 500
    resp = manager_admin.post(
        "/tenant-admin/inventory/stocklot/add/",
        {"stock_item": str(flour.pk), "quantity": "1", "unit": str(units["l"].pk), "notes": ""},
    )
    assert resp.status_code == 200 and "matching unit" in resp.content.decode()


def test_waste_and_meal_forms_post_documents(kitchen, wh, flour, lots, pizza, units):
    client = admin(kitchen, wh)
    resp = client.post(
        "/tenant-admin/inventory/wasteentry/add/",
        {
            "stock_item": str(flour.pk),
            "quantity": "100",
            "unit": str(units["g"].pk),
            "reason": "burnt",
            "lot": "",
            "occurred_on": "2026-09-12",
            "note": "dropped",
        },
    )
    assert resp.status_code == 302, resp.content[:500]
    entry = WasteEntry.objects.get()
    assert entry.reported_by.user == kitchen and entry.restaurant == wh
    member = StaffMember.objects.get(user=kitchen)
    resp = client.post(
        "/tenant-admin/inventory/employeemeal/add/",
        {
            "staff_member": str(member.pk),
            "meal_date": "2026-09-12",
            "note": "",
            "menu_item": str(pizza.pk),
            "quantity": "1",
            "stock_item": "",
            "unit": "",
        },
    )
    assert resp.status_code == 302, resp.content[:500]
    assert EmployeeMeal.objects.get().total_cost > 0
    flour.refresh_from_db()
    assert flour.on_hand_qty == Decimal("1200")
    # posted documents are read-only
    assert client.get(f"/tenant-admin/inventory/wasteentry/{entry.pk}/change/").status_code == 200
    assert client.post(f"/tenant-admin/inventory/wasteentry/{entry.pk}/change/", {"quantity": "1"}).status_code == 403


def test_stock_count_form(manager_admin, flour, lots, units):
    resp = manager_admin.post(
        "/tenant-admin/inventory/stockadjustment/add/",
        {
            "stock_item": str(flour.pk),
            "mode": "count",
            "quantity": "1",
            "unit": str(units["kg"].pk),
            "reason": "count",
            "note": "",
        },
    )
    assert resp.status_code == 302, resp.content[:500]
    assert StockAdjustment.objects.get().delta_base == Decimal("-500")


def test_overview_actions(manager_admin, wh, pizza, flour, lots, order_factory):
    RestaurantDeliveryPlatform.objects.create(restaurant=wh, platform="glovo")
    services.reserve_for_order(order_factory([(pizza, 7, [])]))
    alert = InventoryAlert.objects.get(kind="out_of_stock", menu_item=pizza)
    task = alert.platform_tasks.get()
    resp = manager_admin.post(f"/tenant-admin/inventory/warehouseoverview/tasks/{task.pk}/toggle/")
    assert resp.status_code == 302
    task.refresh_from_db()
    alert.refresh_from_db()
    assert task.is_done and alert.status == "done"  # last platform ticked closes the alert
    lot_a, _ = lots
    resp = manager_admin.post(f"/tenant-admin/inventory/warehouseoverview/lots/{lot_a.pk}/write-off/")
    assert resp.status_code == 302
    lot_a.refresh_from_db()
    assert lot_a.remaining_qty == 0
    assert manager_admin.post("/tenant-admin/inventory/warehouseoverview/recompute/").status_code == 302


def test_overview_actions_require_permission(waiter, wh, pizza, flour, lots, order_factory):
    services.reserve_for_order(order_factory([(pizza, 7, [])]))
    alert = InventoryAlert.objects.get(kind="out_of_stock", menu_item=pizza)
    client = admin(waiter, wh)
    assert client.post(f"/tenant-admin/inventory/warehouseoverview/alerts/{alert.pk}/done/").status_code == 403


def test_recipe_inline_on_menu_item(manager_admin, wh, salad, flour, units):
    url = f"/tenant-admin/menu/menuitem/{salad.pk}/change/"
    html = manager_admin.get(url).content.decode()
    assert "Recipe (ingredients per portion)" in html
    resp = manager_admin.post(
        url,
        {
            "name": "Salad",
            "description": "",
            "category": str(salad.category_id),
            "price": "8.00",
            "is_available": "on",
            "is_featured": "",
            "display_order": "0",
            "preparation_time_minutes": "15",
            "preparation_station": "kitchen",
            "spicy_level": "0",
            "allergens": "[]",
            "track_inventory": "",
            "stock_quantity": "0",
            "modifier_groups_link-TOTAL_FORMS": "0",
            "modifier_groups_link-INITIAL_FORMS": "0",
            "modifier_groups_link-MIN_NUM_FORMS": "0",
            "modifier_groups_link-MAX_NUM_FORMS": "1000",
            "recipe_lines-TOTAL_FORMS": "1",
            "recipe_lines-INITIAL_FORMS": "0",
            "recipe_lines-MIN_NUM_FORMS": "0",
            "recipe_lines-MAX_NUM_FORMS": "1000",
            "recipe_lines-0-stock_item": str(flour.pk),
            "recipe_lines-0-quantity": "150",
            "recipe_lines-0-unit": str(units["g"].pk),
            "recipe_lines-0-note": "",
        },
    )
    assert resp.status_code == 302, resp.content[:800]
    line = RecipeLine.objects.get(menu_item=salad)
    assert line.quantity == Decimal("150")
    # no stock yet -> the dish is auto-disabled right away
    salad.refresh_from_db()
    assert salad.auto_disabled_by_stock is True


def test_recipe_inline_hidden_when_warehouse_off(manager_admin, wh, salad):
    wh.warehouse_enabled = False
    wh.save()
    html = manager_admin.get(f"/tenant-admin/menu/menuitem/{salad.pk}/change/").content.decode()
    assert "Recipe (ingredients per portion)" not in html


def test_settings_toggle_and_platform_inline(wh, manager_admin, pizza, flour, lots, order_factory):
    html = manager_admin.get(f"/tenant-admin/tenants/restaurant/{wh.pk}/change/").content.decode()
    assert 'name="warehouse_enabled"' in html
    assert "delivery_platforms-TOTAL_FORMS" in html
