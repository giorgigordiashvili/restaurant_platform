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
from django.utils.translation import gettext_lazy as _

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
    ("today", _("Today")),
    ("yesterday", _("Yesterday")),
    ("week", _("This week")),
    ("month", _("This month")),
    ("last7", "Last 7 days"),
    ("last30", "Last 30 days"),
)

# (key, title, model, module) in sidebar order.
REPORTS = (
    ("sales", _("Sales"), SalesReport, None),
    ("menu", _("Menu & dishes"), MenuReport, None),
    ("food_cost", _("Food cost"), FoodCostReport, "warehouse"),
    ("staff", _("Staff"), StaffReport, None),
    ("shifts", _("Cash shifts"), ShiftsReport, "cash"),
    ("reservations", _("Reservations"), ReservationsReport, "reservations"),
    ("reviews", _("Reviews"), ReviewsReport, "reviews"),
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
            kpi(_("Net sales"), cur["net_sales"], delta=delta["net_sales"]),
            kpi(_("Orders"), cur["orders"], delta=delta["orders"], kind="int"),
            kpi(_("Average ticket"), cur["avg_ticket"], delta=delta["avg_ticket"]),
            kpi(_("Gross total"), cur["gross_total"], delta=delta["gross_total"]),
            kpi(_("Discounts"), cur["discounts"]),
            kpi(_("Tips"), cur["tips"]),
            kpi(_("Service charge"), cur["service_charge"]),
            kpi(_("Tax"), cur["tax"]),
            kpi(
                _("Cancelled orders"), f"{cur['cancellations']['count']} · {cur['cancellations']['value']}", kind="text"
            ),
            kpi(_("Voided items"), f"{cur['voids']['count']} · {cur['voids']['value']}", kind="text"),
            kpi(_("Refunds"), f"{cur['refunds']['count']} · {cur['refunds']['value']}", kind="text"),
        ]
        by_day = data["by_day"]
        by_hour = data["by_hour"]
        chart_list = [
            {
                "title": _("Sales by day"),
                "type": "line",
                "data": charts.line(
                    [d["day"].strftime("%d %b") for d in by_day],
                    [("Net sales", [d["net"] for d in by_day]), ("Gross", [d["gross"] for d in by_day])],
                ),
            },
            {
                "title": _("Orders by hour"),
                "type": "bar",
                "data": charts.bar([f"{h['hour']:02d}" for h in by_hour], [("Orders", [h["orders"] for h in by_hour])]),
            },
        ]
        tables = [
            Table(
                "by_day",
                "By day",
                [("day", _("Day")), ("orders", _("Orders")), ("net", _("Net sales")), ("gross", _("Gross"))],
                by_day,
                money=("net", "gross"),
            ),
            Table(
                "by_type",
                "By order type",
                [("label", _("Type")), ("orders", _("Orders")), ("net", _("Net sales")), ("gross", _("Gross"))],
                data["by_type"],
                money=("net", "gross"),
            ),
            Table(
                "by_method",
                "By payment method",
                [("label", _("Method")), ("count", _("Payments")), ("amount", _("Amount")), ("tips", _("Tips"))],
                data["by_method"],
                money=("amount", "tips"),
                note="'Unrecorded' is the part of gross sales with no payment in the ledger yet.",
            ),
            Table(
                "by_hour",
                "By hour",
                [("hour", _("Hour")), ("orders", _("Orders")), ("net", _("Net sales"))],
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
            kpi(_("Dishes sold"), sold, kind="int"),
            kpi(_("Distinct dishes"), len(items), kind="int"),
            kpi(_("Top dish"), f"{items[0]['name']} · {items[0]['qty']}" if items else "—", kind="text"),
            kpi(_("Comped lines"), sum(i["comps"] for i in items), kind="int"),
        ]
        chart_list = [
            {
                "title": _("Top 10 dishes by revenue"),
                "type": "bar",
                "data": charts.bar([i["name"] for i in top], [("Net revenue", [i["revenue"] for i in top])]),
            }
        ]
        tables = [
            Table(
                "items",
                "Dishes",
                [
                    ("name", _("Dish")),
                    ("category", _("Category")),
                    ("qty", _("Qty")),
                    ("share_qty", _("Share of qty")),
                    ("revenue", _("Net revenue")),
                    ("share_revenue", _("Share of revenue")),
                    ("avg_price", _("Avg price")),
                    ("modifiers_per_line", _("Modifiers / line")),
                    ("comps", _("Comps")),
                ],
                items,
                money=("revenue", "avg_price"),
                percent=("share_qty", "share_revenue"),
            ),
            Table(
                "categories",
                "Categories",
                [
                    ("category", _("Category")),
                    ("dishes", _("Dishes")),
                    ("qty", _("Qty")),
                    ("revenue", _("Net revenue")),
                    ("share", _("Share")),
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
                        ("name", _("Dish")),
                        ("quadrant", _("Quadrant")),
                        ("qty", _("Qty")),
                        ("share_qty", _("Popularity")),
                        ("revenue", _("Net revenue")),
                        ("cogs", _("COGS")),
                        ("margin_per_unit", _("Margin / portion")),
                        ("food_cost_pct", _("Food cost %")),
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
            kpi(_("Net sales"), fc["net_sales"]),
            kpi(_("Cost of sales (COGS)"), fc["cogs"]),
            kpi(_("Food cost %"), fc["food_cost_pct"], kind="percent"),
            kpi(_("Gross margin"), fc["gross_margin"]),
            kpi(_("Gross margin %"), fc["gross_margin_pct"], kind="percent"),
            kpi(_("Waste"), f"{fc['waste']} · {fc['waste_count']} entries", kind="text"),
            kpi(_("Waste % of sales"), fc["waste_pct"], kind="percent"),
            kpi(_("Employee meals"), fc["employee_meals"]),
            kpi(_("Purchases received"), fc["purchases"]),
        ]
        rows = sorted(data["engineering"]["rows"], key=lambda x: -x["cogs"])
        chart_list = [
            {
                "title": _("Cost breakdown"),
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
                    ("name", _("Dish")),
                    ("qty", _("Qty")),
                    ("revenue", _("Net revenue")),
                    ("cogs", _("COGS")),
                    ("margin", _("Margin")),
                    ("food_cost_pct", _("Food cost %")),
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
            kpi(_("Servers with sales"), len(servers), kind="int"),
            kpi(_("Tips (assigned)"), sum((s["tips"] for s in servers), queries.ZERO)),
            kpi(_("Top server"), f"{servers[0]['name']} · {servers[0]['sales']}" if servers else "—", kind="text"),
        ]
        chart_list = [
            {
                "title": _("Sales by server"),
                "type": "bar",
                "data": charts.bar([s["name"] for s in servers[:12]], [("Sales", [s["sales"] for s in servers[:12]])]),
            }
        ]
        tables = [
            Table(
                "servers",
                "Servers",
                [
                    ("name", _("Server")),
                    ("orders", _("Orders")),
                    ("sales", _("Sales")),
                    ("avg_ticket", _("Avg ticket")),
                    ("tips", _("Tips")),
                ],
                servers,
                money=("sales", "avg_ticket", "tips"),
            ),
            Table(
                "handlers",
                "Who handled what",
                [
                    ("name", _("Staff")),
                    ("orders", _("Orders entered")),
                    ("sales", _("Sales")),
                    ("cancelled", _("Cancelled")),
                    ("discounts", _("Discounts")),
                    ("discounts_amount", _("Discount ₾")),
                    ("voids", _("Voids")),
                    ("voids_amount", _("Void ₾")),
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
            kpi(_("Shifts"), len(rows), kind="int"),
            kpi(_("Sales (closed shifts)"), sum((s["sales"] for s in closed), queries.ZERO)),
            kpi(_("Cash difference"), sum((s["difference"] or 0 for s in closed), queries.ZERO)),
            kpi(_("Refunds"), sum((s["refunds"] for s in closed), queries.ZERO)),
        ]
        for s in rows:
            s["url"] = reverse("tenant_admin:payments_cashshift_change", args=[s["id"]])
        tables = [
            Table(
                "shifts",
                "Shifts",
                [
                    ("number", "#"),
                    ("status", _("Status")),
                    ("opened_by", _("Opened by")),
                    ("opened_at", _("Opened")),
                    ("closed_at", _("Closed")),
                    ("payments", _("Payments")),
                    ("sales", _("Sales")),
                    ("tips", _("Tips")),
                    ("refunds", _("Refunds")),
                    ("expected_cash", _("Expected")),
                    ("counted_cash", _("Counted")),
                    ("difference", _("Difference")),
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
            kpi(_("Reservations"), data["total"], kind="int"),
            kpi(_("Covers"), data["covers"], kind="int"),
            kpi(_("No-shows"), data["no_show"], kind="int"),
            kpi(_("No-show rate"), data["no_show_rate"], kind="percent"),
            kpi(_("Cancelled"), data["cancelled"], kind="int"),
        ]
        by_day = data["by_day"]
        chart_list = [
            {
                "title": _("Reservations & covers by day"),
                "type": "bar",
                "data": charts.bar(
                    [d["day"].strftime("%d %b") for d in by_day],
                    [("Reservations", [d["reservations"] for d in by_day]), ("Covers", [d["covers"] for d in by_day])],
                ),
            }
        ]
        tables = [
            Table(
                "by_day",
                "By day",
                [("day", _("Day")), ("reservations", _("Reservations")), ("covers", _("Covers"))],
                by_day,
            ),
            Table(
                "by_status",
                "By status",
                [("status", _("Status")), ("n", _("Count"))],
                [{"status": k, "n": v} for k, v in data["by_status"].items()],
            ),
            Table("by_source", "By source", [("source", _("Source")), ("n", _("Count"))], data["by_source"]),
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
            kpi(_("Reviews"), data["count"], kind="int"),
            kpi(_("Average rating"), data["average"], kind="text"),
            kpi(_("5 stars"), data["distribution"].get(5, 0), kind="int"),
            kpi(_("1–2 stars"), data["distribution"].get(1, 0) + data["distribution"].get(2, 0), kind="int"),
        ]
        by_day = data["by_day"]
        chart_list = [
            {
                "title": _("Average rating by day"),
                "type": "line",
                "data": charts.line(
                    [d["day"].strftime("%d %b") for d in by_day], [("Average", [d["average"] for d in by_day])]
                ),
            },
            {
                "title": _("Rating distribution"),
                "type": "bar",
                "data": charts.bar(
                    [f"{i} ★" for i in range(1, 6)], [("Reviews", [data["distribution"][i] for i in range(1, 6)])]
                ),
            },
        ]
        tables = [
            Table(
                "by_day", "By day", [("day", _("Day")), ("reviews", _("Reviews")), ("average", _("Average"))], by_day
            ),
            Table(
                "distribution",
                "Distribution",
                [("stars", _("Stars")), ("n", _("Reviews"))],
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
