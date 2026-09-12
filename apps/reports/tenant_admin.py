"""
Report pages in the tenant admin. Each report is a proxy model with a
read-only admin whose changelist renders KPIs, Unfold charts and tables for
the requested period; every table has a CSV download.

``data(request, period)`` returns the JSON-able numbers (shared with the
dashboard API); ``page(request, period, data)`` shapes them for the template.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import path, reverse

from apps.core import modules
from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantModelAdmin, has_resource_permission
from apps.reports import charts, queries
from apps.reports.cache import cached
from apps.reports.exports import csv_response
from apps.reports.models import (
    FoodCostReport,
    MenuReport,
    ReservationsReport,
    ReviewsReport,
    SalesReport,
    ShiftsReport,
    StaffReport,
)
from apps.reports.periods import parse_period
from apps.reports.tables import Table

RANGES = (
    ("today", "Today"),
    ("yesterday", "Yesterday"),
    ("week", "This week"),
    ("month", "This month"),
    ("last7", "Last 7 days"),
    ("last30", "Last 30 days"),
)

# (key, title, model, module) in sidebar order.
REPORTS = (
    ("sales", "Sales", SalesReport, None),
    ("menu", "Menu & dishes", MenuReport, None),
    ("food_cost", "Food cost", FoodCostReport, "warehouse"),
    ("staff", "Staff", StaffReport, None),
    ("shifts", "Cash shifts", ShiftsReport, "cash"),
    ("reservations", "Reservations", ReservationsReport, "reservations"),
    ("reviews", "Reviews", ReviewsReport, "reviews"),
)


def kpi(label, value, *, delta=None, kind="money", url=None):
    return {"label": label, "value": value, "delta": delta, "kind": kind, "url": url}


def report_nav(request):
    """Reports the current user may open, for the page sub-nav."""
    restaurant = request.restaurant
    items = []
    for key, title, model, module in REPORTS:
        if module and not modules.is_enabled(restaurant, module):
            continue
        items.append(
            {
                "key": key,
                "title": title,
                "url": reverse(f"tenant_admin:reports_{model._meta.model_name}_changelist"),
            }
        )
    return items


class ReportAdminBase(ModuleEnabledMixin, TenantModelAdmin):
    permission_resource = "analytics"
    module_code = None
    report_key = ""
    title = ""
    change_list_template = "admin/reports/base.html"
    list_display = ["id"]

    # -- permissions: read-only page ------------------------------------

    def get_queryset(self, request):
        return super().get_queryset(request).none()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        raise PermissionDenied

    # -- report hooks ---------------------------------------------------

    def data(self, request, period) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    def page(self, request, period, data) -> dict:  # pragma: no cover - abstract
        raise NotImplementedError

    def _cached(self, request, name, period, fn):
        return cached(request.restaurant, name, period, fn)

    # -- views ----------------------------------------------------------

    def _guard(self, request):
        if not getattr(request, "restaurant", None) or not self.has_view_permission(request):
            raise PermissionDenied

    def changelist_view(self, request, extra_context=None):
        self._guard(request)
        request.current_app = self.admin_site.name  # {% url 'admin:...' %} must resolve on the tenant site
        period = parse_period(request.GET, request.restaurant)
        data = self.data(request, period)
        page = self.page(request, period, data)
        base_url = reverse(f"tenant_admin:reports_{self.model._meta.model_name}_changelist")
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": self.title,
            "report_key": self.report_key,
            "period": period,
            "ranges": [
                {"key": key, "label": label, "url": f"{base_url}?range={key}", "active": period.key == key}
                for key, label in RANGES
            ],
            "base_url": base_url,
            "export_url": reverse(f"tenant_admin:reports_{self.model._meta.model_name}_export", args=["TABLE"]),
            "query_string": period.query_string(),
            "report_nav": report_nav(request),
            "kpis": page.get("kpis", []),
            "charts": page.get("charts", []),
            "tables": page.get("tables", []),
            "extra": page.get("extra", {}),
            "notices": page.get("notices", []),
        }
        return TemplateResponse(request, self.change_list_template, context)

    def get_urls(self):
        name = self.model._meta.model_name
        custom = [
            path(
                "export/<slug:table>.csv", self.admin_site.admin_view(self.export_view), name=f"reports_{name}_export"
            ),
        ]
        return custom + super().get_urls()

    def export_view(self, request, table):
        self._guard(request)
        period = parse_period(request.GET, request.restaurant)
        page = self.page(request, period, self.data(request, period))
        for t in page.get("tables", []):
            if t.key == table:
                return csv_response(
                    f"{self.report_key}-{table}-{period.start_date}-{period.end_date}.csv", t.columns, t.rows
                )
        raise Http404("Unknown table")


# ── Sales ─────────────────────────────────────────────────────────────────


class SalesReportAdmin(ReportAdminBase):
    report_key = "sales"
    title = "Sales"

    def data(self, request, period):
        r = request.restaurant
        return {
            "summary": self._cached(request, "sales_compare", period, lambda: queries.compare_period(r, period)),
            "by_day": self._cached(request, "sales_by_day", period, lambda: queries.sales_by_day(r, period)),
            "by_hour": self._cached(request, "sales_by_hour", period, lambda: queries.sales_by_hour(r, period)),
            "by_type": self._cached(request, "sales_by_type", period, lambda: queries.sales_by_type(r, period)),
            "by_method": self._cached(request, "sales_by_method", period, lambda: queries.sales_by_method(r, period)),
        }

    def page(self, request, period, data):
        cur = data["summary"]["current"]
        delta = data["summary"]["delta"]
        kpis = [
            kpi("Net sales", cur["net_sales"], delta=delta["net_sales"]),
            kpi("Orders", cur["orders"], delta=delta["orders"], kind="int"),
            kpi("Average ticket", cur["avg_ticket"], delta=delta["avg_ticket"]),
            kpi("Gross total", cur["gross_total"], delta=delta["gross_total"]),
            kpi("Discounts", cur["discounts"]),
            kpi("Tips", cur["tips"]),
            kpi("Service charge", cur["service_charge"]),
            kpi("Tax", cur["tax"]),
            kpi("Cancelled orders", f"{cur['cancellations']['count']} · {cur['cancellations']['value']}", kind="text"),
            kpi("Voided items", f"{cur['voids']['count']} · {cur['voids']['value']}", kind="text"),
            kpi("Refunds", f"{cur['refunds']['count']} · {cur['refunds']['value']}", kind="text"),
        ]
        by_day = data["by_day"]
        by_hour = data["by_hour"]
        chart_list = [
            {
                "title": "Sales by day",
                "type": "line",
                "data": charts.line(
                    [d["day"].strftime("%d %b") for d in by_day],
                    [("Net sales", [d["net"] for d in by_day]), ("Gross", [d["gross"] for d in by_day])],
                ),
            },
            {
                "title": "Orders by hour",
                "type": "bar",
                "data": charts.bar([f"{h['hour']:02d}" for h in by_hour], [("Orders", [h["orders"] for h in by_hour])]),
            },
        ]
        tables = [
            Table(
                "by_day",
                "By day",
                [("day", "Day"), ("orders", "Orders"), ("net", "Net sales"), ("gross", "Gross")],
                by_day,
                money=("net", "gross"),
            ),
            Table(
                "by_type",
                "By order type",
                [("label", "Type"), ("orders", "Orders"), ("net", "Net sales"), ("gross", "Gross")],
                data["by_type"],
                money=("net", "gross"),
            ),
            Table(
                "by_method",
                "By payment method",
                [("label", "Method"), ("count", "Payments"), ("amount", "Amount"), ("tips", "Tips")],
                data["by_method"],
                money=("amount", "tips"),
                note="'Unrecorded' is the part of gross sales with no payment in the ledger yet.",
            ),
            Table(
                "by_hour",
                "By hour",
                [("hour", "Hour"), ("orders", "Orders"), ("net", "Net sales")],
                by_hour,
                money=("net",),
            ),
        ]
        return {"kpis": kpis, "charts": chart_list, "tables": tables}


# ── Menu ──────────────────────────────────────────────────────────────────


class MenuReportAdmin(ReportAdminBase):
    report_key = "menu"
    title = "Menu & dishes"
    change_list_template = "admin/reports/menu.html"

    def data(self, request, period):
        r = request.restaurant
        out = {
            "items": self._cached(request, "items", period, lambda: queries.items_report(r, period)),
            "categories": self._cached(request, "categories", period, lambda: queries.categories_report(r, period)),
        }
        if modules.is_enabled(r, "warehouse"):
            out["engineering"] = self._cached(
                request, "engineering", period, lambda: queries.menu_engineering(r, period)
            )
        return out

    def page(self, request, period, data):
        items = data["items"]
        top = items[:10]
        sold = sum(i["qty"] for i in items)
        kpis = [
            kpi("Dishes sold", sold, kind="int"),
            kpi("Distinct dishes", len(items), kind="int"),
            kpi("Top dish", f"{items[0]['name']} · {items[0]['qty']}" if items else "—", kind="text"),
            kpi("Comped lines", sum(i["comps"] for i in items), kind="int"),
        ]
        chart_list = [
            {
                "title": "Top 10 dishes by revenue",
                "type": "bar",
                "data": charts.bar([i["name"] for i in top], [("Net revenue", [i["revenue"] for i in top])]),
            }
        ]
        tables = [
            Table(
                "items",
                "Dishes",
                [
                    ("name", "Dish"),
                    ("category", "Category"),
                    ("qty", "Qty"),
                    ("share_qty", "Share of qty"),
                    ("revenue", "Net revenue"),
                    ("share_revenue", "Share of revenue"),
                    ("avg_price", "Avg price"),
                    ("modifiers_per_line", "Modifiers / line"),
                    ("comps", "Comps"),
                ],
                items,
                money=("revenue", "avg_price"),
                percent=("share_qty", "share_revenue"),
            ),
            Table(
                "categories",
                "Categories",
                [
                    ("category", "Category"),
                    ("dishes", "Dishes"),
                    ("qty", "Qty"),
                    ("revenue", "Net revenue"),
                    ("share", "Share"),
                ],
                data["categories"],
                money=("revenue",),
                percent=("share",),
            ),
        ]
        extra = {}
        notices = []
        eng = data.get("engineering")
        if eng is not None:
            extra["engineering"] = eng
            tables.append(
                Table(
                    "engineering",
                    "Menu engineering",
                    [
                        ("name", "Dish"),
                        ("quadrant", "Quadrant"),
                        ("qty", "Qty"),
                        ("share_qty", "Popularity"),
                        ("revenue", "Net revenue"),
                        ("cogs", "COGS"),
                        ("margin_per_unit", "Margin / portion"),
                        ("food_cost_pct", "Food cost %"),
                    ],
                    eng["rows"],
                    money=("revenue", "cogs", "margin_per_unit"),
                    percent=("share_qty", "food_cost_pct"),
                    note="Only dishes with a recipe (so a cost exists) are classified.",
                )
            )
        else:
            notices.append("Turn on Warehouse and give dishes recipes to see costs and menu engineering.")
        return {"kpis": kpis, "charts": chart_list, "tables": tables, "extra": extra, "notices": notices}


# ── Food cost ─────────────────────────────────────────────────────────────


class FoodCostReportAdmin(ReportAdminBase):
    report_key = "food_cost"
    title = "Food cost"
    module_code = "warehouse"

    def data(self, request, period):
        r = request.restaurant
        return {
            "food_cost": self._cached(request, "food_cost", period, lambda: queries.food_cost(r, period)),
            "engineering": self._cached(request, "engineering", period, lambda: queries.menu_engineering(r, period)),
        }

    def page(self, request, period, data):
        fc = data["food_cost"]
        kpis = [
            kpi("Net sales", fc["net_sales"]),
            kpi("Cost of sales (COGS)", fc["cogs"]),
            kpi("Food cost %", fc["food_cost_pct"], kind="percent"),
            kpi("Gross margin", fc["gross_margin"]),
            kpi("Gross margin %", fc["gross_margin_pct"], kind="percent"),
            kpi("Waste", f"{fc['waste']} · {fc['waste_count']} entries", kind="text"),
            kpi("Waste % of sales", fc["waste_pct"], kind="percent"),
            kpi("Employee meals", fc["employee_meals"]),
            kpi("Purchases received", fc["purchases"]),
        ]
        rows = sorted(data["engineering"]["rows"], key=lambda x: -x["cogs"])
        chart_list = [
            {
                "title": "Cost breakdown",
                "type": "bar",
                "data": charts.bar(
                    ["COGS", "Waste", "Employee meals", "Gross margin"],
                    [("₾", [fc["cogs"], fc["waste"], fc["employee_meals"], fc["gross_margin"]])],
                ),
            }
        ]
        tables = [
            Table(
                "dishes",
                "Cost by dish",
                [
                    ("name", "Dish"),
                    ("qty", "Qty"),
                    ("revenue", "Net revenue"),
                    ("cogs", "COGS"),
                    ("margin", "Margin"),
                    ("food_cost_pct", "Food cost %"),
                ],
                rows,
                money=("revenue", "cogs", "margin"),
                percent=("food_cost_pct",),
            )
        ]
        return {"kpis": kpis, "charts": chart_list, "tables": tables}


# ── Staff ─────────────────────────────────────────────────────────────────


class StaffReportAdmin(ReportAdminBase):
    report_key = "staff"
    title = "Staff"

    def data(self, request, period):
        r = request.restaurant
        return self._cached(request, "staff", period, lambda: queries.staff_report(r, period))

    def page(self, request, period, data):
        servers = data["servers"]
        kpis = [
            kpi("Servers with sales", len(servers), kind="int"),
            kpi("Tips (assigned)", sum((s["tips"] for s in servers), queries.ZERO)),
            kpi("Top server", f"{servers[0]['name']} · {servers[0]['sales']}" if servers else "—", kind="text"),
        ]
        chart_list = [
            {
                "title": "Sales by server",
                "type": "bar",
                "data": charts.bar([s["name"] for s in servers[:12]], [("Sales", [s["sales"] for s in servers[:12]])]),
            }
        ]
        tables = [
            Table(
                "servers",
                "Servers",
                [
                    ("name", "Server"),
                    ("orders", "Orders"),
                    ("sales", "Sales"),
                    ("avg_ticket", "Avg ticket"),
                    ("tips", "Tips"),
                ],
                servers,
                money=("sales", "avg_ticket", "tips"),
            ),
            Table(
                "handlers",
                "Who handled what",
                [
                    ("name", "Staff"),
                    ("orders", "Orders entered"),
                    ("sales", "Sales"),
                    ("cancelled", "Cancelled"),
                    ("discounts", "Discounts"),
                    ("discounts_amount", "Discount ₾"),
                    ("voids", "Voids"),
                    ("voids_amount", "Void ₾"),
                ],
                data["handlers"],
                money=("sales", "discounts_amount", "voids_amount"),
            ),
        ]
        return {"kpis": kpis, "charts": chart_list, "tables": tables}


# ── Shifts ────────────────────────────────────────────────────────────────


class ShiftsReportAdmin(ReportAdminBase):
    report_key = "shifts"
    title = "Cash shifts"
    module_code = "cash"

    def data(self, request, period):
        r = request.restaurant
        return {"shifts": self._cached(request, "shifts", period, lambda: queries.shifts_report(r, period))}

    def page(self, request, period, data):
        rows = data["shifts"]
        closed = [s for s in rows if s["status"] == "closed"]
        kpis = [
            kpi("Shifts", len(rows), kind="int"),
            kpi("Sales (closed shifts)", sum((s["sales"] for s in closed), queries.ZERO)),
            kpi("Cash difference", sum((s["difference"] or 0 for s in closed), queries.ZERO)),
            kpi("Refunds", sum((s["refunds"] for s in closed), queries.ZERO)),
        ]
        for s in rows:
            s["url"] = reverse("tenant_admin:payments_cashshift_change", args=[s["id"]])
        tables = [
            Table(
                "shifts",
                "Shifts",
                [
                    ("number", "#"),
                    ("status", "Status"),
                    ("opened_by", "Opened by"),
                    ("opened_at", "Opened"),
                    ("closed_at", "Closed"),
                    ("payments", "Payments"),
                    ("sales", "Sales"),
                    ("tips", "Tips"),
                    ("refunds", "Refunds"),
                    ("expected_cash", "Expected"),
                    ("counted_cash", "Counted"),
                    ("difference", "Difference"),
                ],
                rows,
                money=("sales", "tips", "refunds", "expected_cash", "counted_cash", "difference"),
            )
        ]
        return {"kpis": kpis, "charts": [], "tables": tables}


# ── Reservations ──────────────────────────────────────────────────────────


class ReservationsReportAdmin(ReportAdminBase):
    report_key = "reservations"
    title = "Reservations"
    module_code = "reservations"

    def data(self, request, period):
        r = request.restaurant
        return self._cached(request, "reservations", period, lambda: queries.reservations_report(r, period))

    def page(self, request, period, data):
        kpis = [
            kpi("Reservations", data["total"], kind="int"),
            kpi("Covers", data["covers"], kind="int"),
            kpi("No-shows", data["no_show"], kind="int"),
            kpi("No-show rate", data["no_show_rate"], kind="percent"),
            kpi("Cancelled", data["cancelled"], kind="int"),
        ]
        by_day = data["by_day"]
        chart_list = [
            {
                "title": "Reservations & covers by day",
                "type": "bar",
                "data": charts.bar(
                    [d["day"].strftime("%d %b") for d in by_day],
                    [("Reservations", [d["reservations"] for d in by_day]), ("Covers", [d["covers"] for d in by_day])],
                ),
            }
        ]
        tables = [
            Table("by_day", "By day", [("day", "Day"), ("reservations", "Reservations"), ("covers", "Covers")], by_day),
            Table(
                "by_status",
                "By status",
                [("status", "Status"), ("n", "Count")],
                [{"status": k, "n": v} for k, v in data["by_status"].items()],
            ),
            Table("by_source", "By source", [("source", "Source"), ("n", "Count")], data["by_source"]),
        ]
        return {"kpis": kpis, "charts": chart_list, "tables": tables}


# ── Reviews ───────────────────────────────────────────────────────────────


class ReviewsReportAdmin(ReportAdminBase):
    report_key = "reviews"
    title = "Reviews"
    module_code = "reviews"

    def data(self, request, period):
        r = request.restaurant
        return self._cached(request, "reviews", period, lambda: queries.reviews_report(r, period))

    def page(self, request, period, data):
        kpis = [
            kpi("Reviews", data["count"], kind="int"),
            kpi("Average rating", data["average"], kind="text"),
            kpi("5 stars", data["distribution"].get(5, 0), kind="int"),
            kpi("1–2 stars", data["distribution"].get(1, 0) + data["distribution"].get(2, 0), kind="int"),
        ]
        by_day = data["by_day"]
        chart_list = [
            {
                "title": "Average rating by day",
                "type": "line",
                "data": charts.line(
                    [d["day"].strftime("%d %b") for d in by_day], [("Average", [d["average"] for d in by_day])]
                ),
            },
            {
                "title": "Rating distribution",
                "type": "bar",
                "data": charts.bar(
                    [f"{i} ★" for i in range(1, 6)], [("Reviews", [data["distribution"][i] for i in range(1, 6)])]
                ),
            },
        ]
        tables = [
            Table("by_day", "By day", [("day", "Day"), ("reviews", "Reviews"), ("average", "Average")], by_day),
            Table(
                "distribution",
                "Distribution",
                [("stars", "Stars"), ("n", "Reviews")],
                [{"stars": i, "n": data["distribution"][i]} for i in range(5, 0, -1)],
            ),
        ]
        return {"kpis": kpis, "charts": chart_list, "tables": tables}


ADMINS = {
    "sales": SalesReportAdmin,
    "menu": MenuReportAdmin,
    "food_cost": FoodCostReportAdmin,
    "staff": StaffReportAdmin,
    "shifts": ShiftsReportAdmin,
    "reservations": ReservationsReportAdmin,
    "reviews": ReviewsReportAdmin,
}


def register_reports_admin(site):
    for key, _title, model, _module in REPORTS:
        site.register(model, ADMINS[key])


def report_data(request, key: str, period) -> dict:
    """JSON-able numbers for one report (used by the dashboard API)."""
    admin_class = ADMINS[key]
    model = next(m for k, _t, m, _mod in REPORTS if k == key)
    from apps.core.admin_sites import tenant_admin_site

    instance = admin_class(model, tenant_admin_site)
    return instance.data(request, period)


def report_module(key: str):
    return next(mod for k, _t, _m, mod in REPORTS if k == key)


__all__ = ["register_reports_admin", "report_data", "report_module", "report_nav", "REPORTS", "has_resource_permission"]
