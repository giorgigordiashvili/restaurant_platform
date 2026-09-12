"""
Tenant dashboard (the admin index): one card per enabled module the user may
read, with a couple of indexed counts and quick links.
"""

from __future__ import annotations

from django.db.models import F
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.core import modules
from apps.core.tenant_admin_base import has_resource_permission


def _url(name, **kwargs):
    try:
        return reverse(f"tenant_admin:{name}", kwargs=kwargs or None)
    except NoReverseMatch:
        return None


def _link(title, name):
    url = _url(name)
    return {"title": title, "url": url} if url else None


def module_cards(request):
    restaurant = request.restaurant
    today = timezone.localdate()
    on = modules.enabled_codes(restaurant)
    cards = []

    def card(code, title, icon, stats, links, resource):
        if code and code not in on:
            return
        if not has_resource_permission(request, resource, "read"):
            return
        cards.append(
            {
                "code": code or resource,
                "title": title,
                "icon": icon,
                "stats": [s for s in stats if s],
                "links": [link for link in links if link],
            }
        )

    if "ordering" in on and has_resource_permission(request, "orders", "read"):
        from apps.orders.models import Order

        qs = Order.objects.filter(restaurant=restaurant)
        card(
            "ordering",
            "Orders",
            "receipt_long",
            [
                {
                    "label": "Today",
                    "value": qs.filter(created_at__date=today).count(),
                    "url": _url("orders_order_changelist"),
                },
                {
                    "label": "Open now",
                    "value": qs.filter(status__in=("pending", "confirmed", "preparing", "ready")).count(),
                    "url": _url("orders_order_changelist"),
                },
            ],
            [_link("All orders", "orders_order_changelist")],
            "orders",
        )

    if "reservations" in on and has_resource_permission(request, "reservations", "read"):
        from apps.reservations.models import Reservation

        qs = Reservation.objects.filter(restaurant=restaurant)
        card(
            "reservations",
            "Reservations",
            "event_seat",
            [
                {
                    "label": "Today",
                    "value": qs.filter(reservation_date=today, status__in=("pending", "confirmed", "seated")).count(),
                    "url": _url("reservations_reservation_changelist"),
                },
                {
                    "label": "Awaiting confirmation",
                    "value": qs.filter(status="pending", reservation_date__gte=today).count(),
                    "url": _url("reservations_reservation_changelist"),
                },
            ],
            [
                _link("Reservations", "reservations_reservation_changelist"),
                _link("Blocked times", "reservations_reservationblockedtime_changelist"),
            ],
            "reservations",
        )

    if "tables" in on and has_resource_permission(request, "tables", "read"):
        from apps.tables.models import Table, TableSession

        card(
            "tables",
            "Tables & QR",
            "table_restaurant",
            [
                {
                    "label": "Active sessions",
                    "value": TableSession.objects.filter(table__restaurant=restaurant, status="active").count(),
                    "url": _url("tables_tablesession_changelist"),
                },
                {
                    "label": "Tables",
                    "value": Table.objects.filter(restaurant=restaurant, is_active=True).count(),
                    "url": _url("tables_table_changelist"),
                },
            ],
            [_link("Tables", "tables_table_changelist"), _link("QR codes", "tables_tableqrcode_changelist")],
            "tables",
        )

    if "cash" in on and has_resource_permission(request, "cash", "read"):
        from django.db.models import Sum

        from apps.payments import services as ledger
        from apps.payments.models import Payment

        shift = ledger.current_shift(restaurant)
        today_qs = Payment.objects.filter(
            restaurant=restaurant, status__in=ledger.PAID_STATUSES, completed_at__date=today
        )
        stats = [
            {
                "label": "Open shift" if shift else "Shift",
                "value": f"#{shift.number}" if shift else "closed",
                "url": (
                    _url("payments_cashshift_change", object_id=shift.pk)
                    if shift
                    else _url("payments_cashshift_changelist")
                ),
            },
            {
                "label": "Taken today",
                "value": today_qs.aggregate(s=Sum("total_amount"))["s"] or 0,
                "url": _url("payments_payment_changelist"),
            },
        ]
        if shift:
            stats.append(
                {
                    "label": "Cash in drawer",
                    "value": ledger.x_report(shift)["expected_cash"],
                    "url": _url("payments_cashshift_change", object_id=shift.pk),
                }
            )
        card(
            "cash",
            "Cash & payments",
            "point_of_sale",
            stats,
            [
                _link("Shifts", "payments_cashshift_changelist"),
                _link("Payments", "payments_payment_changelist"),
                _link("Reasons", "payments_discountreason_changelist"),
            ],
            "cash",
        )

    if "warehouse" in on and has_resource_permission(request, "warehouse", "read"):
        from apps.inventory.models import InventoryAlert, StockItem

        card(
            "warehouse",
            "Warehouse",
            "inventory_2",
            [
                {
                    "label": "Low / out",
                    "value": StockItem.objects.filter(restaurant=restaurant, is_active=True)
                    .filter(on_hand_qty__lte=F("reserved_qty") + F("min_level"))
                    .count(),
                    "url": _url("inventory_warehouseoverview_changelist"),
                },
                {
                    "label": "Open alerts",
                    "value": InventoryAlert.objects.filter(restaurant=restaurant, status="open").count(),
                    "url": _url("inventory_warehouseoverview_changelist"),
                },
            ],
            [
                _link("Overview", "inventory_warehouseoverview_changelist"),
                _link("Receive stock", "inventory_stocklot_add"),
            ],
            "warehouse",
        )

    if has_resource_permission(request, "menu", "read"):
        from apps.menu.models import MenuItem

        qs = MenuItem.objects.filter(restaurant=restaurant)
        stats = [{"label": "Dishes", "value": qs.count(), "url": _url("menu_menuitem_changelist")}]
        if "warehouse" in on:
            stats.append(
                {
                    "label": "Sold out",
                    "value": qs.filter(auto_disabled_by_stock=True).count(),
                    "url": _url("menu_menuitem_changelist") + "?auto_disabled_by_stock__exact=1",
                }
            )
        card(
            "menu",
            "Menu",
            "restaurant_menu",
            stats,
            [_link("Items", "menu_menuitem_changelist"), _link("Categories", "menu_menucategory_changelist")],
            "menu",
        )

    if "loyalty" in on and has_resource_permission(request, "menu", "read"):
        from apps.loyalty.models import LoyaltyProgram

        card(
            "loyalty",
            "Loyalty",
            "loyalty",
            [
                {
                    "label": "Active programs",
                    "value": LoyaltyProgram.objects.filter(restaurant=restaurant, is_active=True).count(),
                    "url": _url("loyalty_loyaltyprogram_changelist"),
                }
            ],
            [_link("Programs", "loyalty_loyaltyprogram_changelist")],
            "menu",
        )

    if "reviews" in on and has_resource_permission(request, "menu", "read"):
        from apps.reviews.models import Review

        card(
            "reviews",
            "Reviews",
            "reviews",
            [
                {
                    "label": "Last 7 days",
                    "value": Review.objects.filter(
                        restaurant=restaurant, created_at__gte=timezone.now() - timezone.timedelta(days=7)
                    ).count(),
                    "url": _url("reviews_review_changelist"),
                }
            ],
            [_link("Reviews", "reviews_review_changelist")],
            "menu",
        )

    if "fiscal" in on and has_resource_permission(request, "fiscal", "read"):
        from apps.fiscal.models import FiscalDocument

        qs = FiscalDocument.objects.filter(restaurant=restaurant)
        card(
            "fiscal",
            "Fiscal & VAT",
            "receipt",
            [
                {
                    "label": "Receipts today",
                    "value": qs.filter(kind="receipt", created_at__date=today).count(),
                    "url": _url("fiscal_fiscaldocument_changelist"),
                },
                {
                    "label": "Failed",
                    "value": qs.filter(status="failed").count(),
                    "url": _url("fiscal_fiscaldocument_changelist") + "?status__exact=failed",
                },
            ],
            [
                _link("Documents", "fiscal_fiscaldocument_changelist"),
                _link("Fiscal settings", "fiscal_fiscalsettingspage_changelist"),
            ],
            "fiscal",
        )

    if "printing" in on and has_resource_permission(request, "settings", "read"):
        from apps.printing import services as printing

        st = printing.printer_status(restaurant)
        card(
            "printing",
            "Printing",
            "print",
            [
                {
                    "label": "Printers offline",
                    "value": len(st["offline"]),
                    "url": _url("printing_printer_changelist"),
                },
                {
                    "label": "Failed jobs",
                    "value": st["failed_jobs"],
                    "url": _url("printing_printjob_changelist") + "?status__exact=failed",
                },
            ],
            [_link("Printers", "printing_printer_changelist"), _link("Print jobs", "printing_printjob_changelist")],
            "settings",
        )

    if has_resource_permission(request, "analytics", "read"):
        from apps.reports import queries
        from apps.reports.periods import parse_period

        period = parse_period({"range": "today"}, restaurant)
        summary = queries.sales_summary(restaurant, period)
        card(
            None,
            "Reports",
            "monitoring",
            [
                {
                    "label": "Sales today",
                    "value": f"{summary['net_sales']} ₾",
                    "url": _url("reports_salesreport_changelist"),
                },
                {"label": "Orders today", "value": summary["orders"], "url": _url("reports_salesreport_changelist")},
            ],
            [
                _link("Sales", "reports_salesreport_changelist"),
                _link("Menu", "reports_menureport_changelist"),
                _link("Staff", "reports_staffreport_changelist"),
            ],
            "analytics",
        )

    if has_resource_permission(request, "staff", "read"):
        from apps.staff.models import StaffInvitation, StaffMember

        card(
            None,
            "Staff",
            "badge",
            [
                {
                    "label": "Members",
                    "value": StaffMember.objects.filter(restaurant=restaurant, is_active=True).count(),
                    "url": _url("staff_staffmember_changelist"),
                },
                {
                    "label": "Pending invitations",
                    "value": StaffInvitation.objects.filter(restaurant=restaurant, status="pending").count(),
                    "url": _url("staff_staffinvitation_changelist"),
                },
            ],
            [
                _link("Members", "staff_staffmember_changelist"),
                _link("Invite", "staff_staffinvitation_add"),
                _link("Roles", "staff_staffrole_changelist"),
            ],
            "staff",
        )

    if has_resource_permission(request, "settings", "read"):
        card(
            None,
            "Settings",
            "settings",
            [{"label": "Modules on", "value": len(on), "url": _url("tenants_restaurantmodules_changelist")}],
            [
                _link("Restaurant settings", "tenants_restaurant_changelist"),
                _link("Modules", "tenants_restaurantmodules_changelist"),
            ],
            "settings",
        )
    return cards
