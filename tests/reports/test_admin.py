"""Report pages in the tenant admin + the dashboard API."""

import json
import re
from html import unescape

from django.test import Client

import pytest

from apps.core import modules


def _admin(user, restaurant):
    user.is_staff = True
    user.save()
    c = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
    c.force_login(user)
    return c


@pytest.fixture
def owner_admin(user, restaurant, staff_roles, create_staff_member):
    create_staff_member(user=user, restaurant=restaurant, role=next(r for r in staff_roles if r.name == "owner"))
    return _admin(user, restaurant)


@pytest.fixture
def waiter_admin(waiter_staff, restaurant):
    return _admin(waiter_staff.user, restaurant)


SALES = "/tenant-admin/reports/salesreport/"


@pytest.mark.django_db
class TestPages:
    def test_sales_page_renders_kpis_charts_tables(self, owner_admin, restaurant, create_order, create_order_item):
        o = create_order(restaurant=restaurant, status="completed")
        create_order_item(order=o, item_name="Khinkali", unit_price=10, quantity=2)
        o.calculate_totals()
        resp = owner_admin.get(SALES + "?range=today")
        assert resp.status_code == 200
        html = resp.content.decode()
        assert 'data-testid="report-sales"' in html and 'data-testid="kpis"' in html
        canvases = re.findall(r'<canvas class="chart"[^>]*data-value="([^"]+)"', html)
        assert len(canvases) == 2
        payload = json.loads(unescape(canvases[0]))
        assert payload["datasets"][0]["label"] == "Net sales" and payload["datasets"][0]["data"] == [20.0]
        assert 'data-testid="table-by_method"' in html and "Unrecorded" in html

    def test_every_report_page_renders_for_the_owner(self, owner_admin, restaurant, user):
        for code in ("warehouse", "reservations", "reviews", "cash"):
            if not modules.is_enabled(restaurant, code):
                modules.set_module(restaurant, code, True, by=user)
        for name in (
            "salesreport",
            "menureport",
            "foodcostreport",
            "staffreport",
            "shiftsreport",
            "reservationsreport",
            "reviewsreport",
        ):
            resp = owner_admin.get(f"/tenant-admin/reports/{name}/?range=week")
            assert resp.status_code == 200, name

    def test_waiter_has_no_reports(self, waiter_admin):
        assert waiter_admin.get(SALES).status_code == 403
        html = waiter_admin.get("/tenant-admin/").content.decode()
        assert "/tenant-admin/reports/" not in html

    def test_food_cost_follows_the_warehouse_module(self, owner_admin, restaurant, user):
        assert owner_admin.get("/tenant-admin/reports/foodcostreport/").status_code == 403
        modules.set_module(restaurant, "warehouse", True, by=user)
        assert owner_admin.get("/tenant-admin/reports/foodcostreport/").status_code == 200
        html = owner_admin.get("/tenant-admin/").content.decode()
        assert "/tenant-admin/reports/foodcostreport/" in html

    def test_menu_page_notice_without_warehouse(self, owner_admin, restaurant):
        html = owner_admin.get("/tenant-admin/reports/menureport/").content.decode()
        assert "Turn on Warehouse" in html

    def test_csv_export(self, owner_admin, restaurant, create_order, create_order_item):
        o = create_order(restaurant=restaurant, status="completed", order_type="takeaway")
        create_order_item(order=o, item_name="Khinkali", unit_price=10, quantity=2)
        o.calculate_totals()
        resp = owner_admin.get(SALES + "export/by_type.csv?range=today")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")
        assert 'filename="sales-by_type-' in resp["Content-Disposition"]
        lines = resp.content.decode("utf-8-sig").strip().splitlines()
        assert lines[0] == "Type,Orders,Net sales,Gross"
        assert lines[1].startswith("Takeaway,1,20.00,")
        assert owner_admin.get(SALES + "export/nope.csv").status_code == 404

    def test_dashboard_card(self, owner_admin):
        html = owner_admin.get("/tenant-admin/").content.decode()
        assert 'data-testid="card-analytics"' in html and "Sales today" in html


@pytest.mark.django_db
class TestApi:
    def test_sales_api(self, authenticated_owner_client, restaurant, create_order, create_order_item):
        o = create_order(restaurant=restaurant, status="completed")
        create_order_item(order=o, item_name="Khinkali", unit_price=10, quantity=2)
        o.calculate_totals()
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        resp = authenticated_owner_client.get("/api/v1/dashboard/reports/sales/?range=today")
        assert resp.status_code == 200, resp.content
        data = resp.json().get("data") or resp.json()
        assert float(data["summary"]["current"]["net_sales"]) == 20.0
        assert data["period"]["key"] == "today" and data["period"]["tz"] == "Asia/Tbilisi"
        assert len(data["by_hour"]) == 24

    def test_module_disabled_and_unknown(self, authenticated_owner_client, restaurant):
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        resp = authenticated_owner_client.get("/api/v1/dashboard/reports/food_cost/")
        assert resp.status_code == 404 and resp.json()["error"]["code"] == "module_disabled"
        assert authenticated_owner_client.get("/api/v1/dashboard/reports/nope/").status_code == 404

    def test_waiter_forbidden(self, authenticated_waiter_client, waiter_staff, restaurant):
        authenticated_waiter_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        assert authenticated_waiter_client.get("/api/v1/dashboard/reports/sales/").status_code == 403
