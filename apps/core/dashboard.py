"""
Tenant dashboard (the admin index): one card per enabled module the user may
read, with a couple of indexed counts and quick links.
"""

from __future__ import annotations

from django.db.models import F
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

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
            _("Orders"),
            "receipt_long",
            [
                {
                    "label": _("Today"),
                    "value": qs.filter(created_at__date=today).count(),
                    "url": _url("orders_order_changelist"),
                },
                {
                    "label": _("Open now"),
                    "value": qs.filter(status__in=("pending", "confirmed", "preparing", "ready")).count(),
                    "url": _url("orders_order_changelist"),
                },
            ],
            [_link(_("All orders"), "orders_order_changelist")],
            "orders",
        )

    if "reservations" in on and has_resource_permission(request, "reservations", "read"):
        from apps.reservations.models import Reservation

        qs = Reservation.objects.filter(restaurant=restaurant)
        card(
            "reservations",
            _("Reservations"),
            "event_seat",
            [
                {
                    "label": _("Today"),
                    "value": qs.filter(reservation_date=today, status__in=("pending", "confirmed", "seated")).count(),
                    "url": _url("reservations_reservation_changelist"),
                },
                {
                    "label": _("Awaiting confirmation"),
                    "value": qs.filter(status="pending", reservation_date__gte=today).count(),
                    "url": _url("reservations_reservation_changelist"),
                },
            ],
            [
                _link(_("Reservations"), "reservations_reservation_changelist"),
                _link(_("Blocked times"), "reservations_reservationblockedtime_changelist"),
            ],
            "reservations",
        )

    if "tables" in on and has_resource_permission(request, "tables", "read"):
        from apps.tables.models import Table, TableSession

        card(
            "tables",
            _("Tables & QR"),
            "table_restaurant",
            [
                {
                    "label": _("Active sessions"),
                    "value": TableSession.objects.filter(table__restaurant=restaurant, status="active").count(),
                    "url": _url("tables_tablesession_changelist"),
                },
                {
                    "label": _("Tables"),
                    "value": Table.objects.filter(restaurant=restaurant, is_active=True).count(),
                    "url": _url("tables_table_changelist"),
                },
            ],
            [_link(_("Tables"), "tables_table_changelist"), _link(_("QR codes"), "tables_tableqrcode_changelist")],
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
                "label": _("Open shift") if shift else "Shift",
                "value": f"#{shift.number}" if shift else "closed",
                "url": (
                    _url("payments_cashshift_change", object_id=shift.pk)
                    if shift
                    else _url("payments_cashshift_changelist")
                ),
            },
            {
                "label": _("Taken today"),
                "value": today_qs.aggregate(s=Sum("total_amount"))["s"] or 0,
                "url": _url("payments_payment_changelist"),
            },
        ]
        if shift:
            stats.append(
                {
                    "label": _("Cash in drawer"),
                    "value": ledger.x_report(shift)["expected_cash"],
                    "url": _url("payments_cashshift_change", object_id=shift.pk),
                }
            )
        card(
            "cash",
            _("Cash & payments"),
            "point_of_sale",
            stats,
            [
                _link(_("Shifts"), "payments_cashshift_changelist"),
                _link(_("Payments"), "payments_payment_changelist"),
                _link(_("Reasons"), "payments_discountreason_changelist"),
            ],
            "cash",
        )

    if "warehouse" in on and has_resource_permission(request, "warehouse", "read"):
        from apps.inventory.models import InventoryAlert, StockItem

        card(
            "warehouse",
            _("Warehouse"),
            "inventory_2",
            [
                {
                    "label": _("Low / out"),
                    "value": StockItem.objects.filter(restaurant=restaurant, is_active=True)
                    .filter(on_hand_qty__lte=F("reserved_qty") + F("min_level"))
                    .count(),
                    "url": _url("inventory_warehouseoverview_changelist"),
                },
                {
                    "label": _("Open alerts"),
                    "value": InventoryAlert.objects.filter(restaurant=restaurant, status="open").count(),
                    "url": _url("inventory_warehouseoverview_changelist"),
                },
            ],
            [
                _link(_("Overview"), "inventory_warehouseoverview_changelist"),
                _link(_("Receive stock"), "inventory_stocklot_add"),
            ],
            "warehouse",
        )

    if has_resource_permission(request, "menu", "read"):
        from apps.menu.models import MenuItem

        qs = MenuItem.objects.filter(restaurant=restaurant)
        stats = [{"label": _("Dishes"), "value": qs.count(), "url": _url("menu_menuitem_changelist")}]
        if "warehouse" in on:
            stats.append(
                {
                    "label": _("Sold out"),
                    "value": qs.filter(auto_disabled_by_stock=True).count(),
                    "url": _url("menu_menuitem_changelist") + "?auto_disabled_by_stock__exact=1",
                }
            )
        card(
            "menu",
            _("Menu"),
            "restaurant_menu",
            stats,
            [_link(_("Items"), "menu_menuitem_changelist"), _link(_("Categories"), "menu_menucategory_changelist")],
            "menu",
        )

    if "loyalty" in on and has_resource_permission(request, "menu", "read"):
        from apps.loyalty.models import LoyaltyProgram

        card(
            "loyalty",
            _("Loyalty"),
            "loyalty",
            [
                {
                    "label": _("Active programs"),
                    "value": LoyaltyProgram.objects.filter(restaurant=restaurant, is_active=True).count(),
                    "url": _url("loyalty_loyaltyprogram_changelist"),
                }
            ],
            [_link(_("Programs"), "loyalty_loyaltyprogram_changelist")],
            "menu",
        )

    if "reviews" in on and has_resource_permission(request, "menu", "read"):
        from apps.reviews.models import Review

        card(
            "reviews",
            _("Reviews"),
            "reviews",
            [
                {
                    "label": _("Last 7 days"),
                    "value": Review.objects.filter(
                        restaurant=restaurant, created_at__gte=timezone.now() - timezone.timedelta(days=7)
                    ).count(),
                    "url": _url("reviews_review_changelist"),
                }
            ],
            [_link(_("Reviews"), "reviews_review_changelist")],
            "menu",
        )

    if "delivery" in on and has_resource_permission(request, "orders", "read"):
        from apps.orders.models import Order

        qs = Order.objects.filter(restaurant=restaurant, source__in=("glovo", "wolt", "bolt_food"))
        card(
            "delivery",
            _("Delivery platforms"),
            "delivery_dining",
            [
                {
                    "label": _("Platform orders today"),
                    "value": qs.filter(created_at__date=today).count(),
                    "url": _url("orders_order_changelist") + "?source__exact=glovo",
                },
                {
                    "label": _("Awaiting accept"),
                    "value": qs.filter(status="pending").count(),
                    "url": _url("orders_order_changelist") + "?status__exact=pending",
                },
            ],
            [
                _link(_("Platforms"), "delivery_deliveryplatformspage_changelist"),
                _link(_("Setup guide"), "delivery_deliveryplatformspage_guide"),
                _link(_("Orders"), "orders_order_changelist"),
            ],
            "orders",
        )

    if "crm" in on and has_resource_permission(request, "crm", "read"):
        from apps.crm import services as crm_services

        cs = crm_services.summary(restaurant)
        card(
            "crm",
            _("Guests"),
            "campaign",
            [
                {"label": _("Guests"), "value": cs["customers"], "url": _url("crm_customer_changelist")},
                {
                    "label": _("Opted in"),
                    "value": cs["opted_in"],
                    "url": _url("crm_customer_changelist") + "?marketing_opt_in__exact=1",
                },
                {"label": _("New (30 days)"), "value": cs["new_30d"], "url": _url("crm_customer_changelist")},
            ],
            [
                _link(_("Customers"), "crm_customer_changelist"),
                _link(_("Campaigns"), "crm_campaign_changelist"),
                _link(_("Automations"), "crm_automation_changelist"),
            ],
            "crm",
        )

    if "timekeeping" in on and has_resource_permission(request, "timekeeping", "read"):
        from apps.timekeeping import services as tk
        from apps.timekeeping.models import RotaShift

        week = tk.week_start(today)
        card(
            "timekeeping",
            _("Timekeeping"),
            "schedule",
            [
                {
                    "label": _("Clocked in now"),
                    "value": len(tk.whos_in(restaurant)),
                    "url": _url("timekeeping_timeentry_changelist"),
                },
                {
                    "label": _("Unpublished shifts this week"),
                    "value": RotaShift.objects.filter(restaurant=restaurant, published=False, date__gte=week).count(),
                    "url": _url("timekeeping_rotashift_changelist"),
                },
            ],
            [
                _link(_("Rota"), "timekeeping_rotashift_changelist"),
                _link(_("Time entries"), "timekeeping_timeentry_changelist"),
                _link(_("Hours"), "reports_hoursreport_changelist"),
            ],
            "timekeeping",
        )

    if "purchasing" in on and has_resource_permission(request, "warehouse", "read"):
        from apps.purchasing import services as purchasing_services

        ps = purchasing_services.open_summary(restaurant)
        card(
            "purchasing",
            _("Purchasing"),
            "local_shipping",
            [
                {
                    "label": _("Open orders"),
                    "value": ps["open"],
                    "url": _url("purchasing_purchaseorder_changelist") + "?status__exact=sent",
                },
                {"label": _("Due today"), "value": ps["due_today"], "url": _url("purchasing_purchaseorder_changelist")},
                {"label": _("Suppliers"), "value": ps["suppliers"], "url": _url("purchasing_supplier_changelist")},
            ],
            [
                _link(_("Purchase orders"), "purchasing_purchaseorder_changelist"),
                _link(_("Suppliers"), "purchasing_supplier_changelist"),
            ],
            "warehouse",
        )

    if "promotions" in on and has_resource_permission(request, "menu", "read"):
        from apps.promotions import services as promo_services
        from apps.promotions.models import Promotion, PromotionUse

        live = len(promo_services.live_happy_hours(restaurant))
        card(
            "promotions",
            _("Promotions"),
            "sell",
            [
                {
                    "label": _("Active"),
                    "value": Promotion.objects.filter(restaurant=restaurant, is_active=True).count(),
                    "url": _url("promotions_promotion_changelist"),
                },
                {"label": _("Happy hours live now"), "value": live, "url": _url("promotions_promotion_changelist")},
                {
                    "label": _("Codes used today"),
                    "value": PromotionUse.objects.filter(
                        promotion__restaurant=restaurant, promotion__kind="promo_code", created_at__date=today
                    ).count(),
                    "url": _url("promotions_promotion_changelist"),
                },
            ],
            [
                _link(_("Promotions"), "promotions_promotion_changelist"),
                _link(_("Schedules"), "promotions_menuschedule_changelist"),
            ],
            "menu",
        )

    if "fiscal" in on and has_resource_permission(request, "fiscal", "read"):
        from apps.fiscal.models import FiscalDocument

        qs = FiscalDocument.objects.filter(restaurant=restaurant)
        card(
            "fiscal",
            _("Fiscal & VAT"),
            "receipt",
            [
                {
                    "label": _("Receipts today"),
                    "value": qs.filter(kind="receipt", created_at__date=today).count(),
                    "url": _url("fiscal_fiscaldocument_changelist"),
                },
                {
                    "label": _("Failed"),
                    "value": qs.filter(status="failed").count(),
                    "url": _url("fiscal_fiscaldocument_changelist") + "?status__exact=failed",
                },
            ],
            [
                _link(_("Documents"), "fiscal_fiscaldocument_changelist"),
                _link(_("Fiscal settings"), "fiscal_fiscalsettingspage_changelist"),
            ],
            "fiscal",
        )

    if "printing" in on and has_resource_permission(request, "settings", "read"):
        from apps.printing import services as printing

        st = printing.printer_status(restaurant)
        card(
            "printing",
            _("Printing"),
            "print",
            [
                {
                    "label": _("Printers offline"),
                    "value": len(st["offline"]),
                    "url": _url("printing_printer_changelist"),
                },
                {
                    "label": _("Failed jobs"),
                    "value": st["failed_jobs"],
                    "url": _url("printing_printjob_changelist") + "?status__exact=failed",
                },
            ],
            [
                _link(_("Printers"), "printing_printer_changelist"),
                _link(_("Print jobs"), "printing_printjob_changelist"),
            ],
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
                    "label": _("Sales today"),
                    "value": f"{summary['net_sales']} ₾",
                    "url": _url("reports_salesreport_changelist"),
                },
                {"label": _("Orders today"), "value": summary["orders"], "url": _url("reports_salesreport_changelist")},
            ],
            [
                _link(_("Sales"), "reports_salesreport_changelist"),
                _link(_("Menu"), "reports_menureport_changelist"),
                _link(_("Staff"), "reports_staffreport_changelist"),
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
                    "label": _("Members"),
                    "value": StaffMember.objects.filter(restaurant=restaurant, is_active=True).count(),
                    "url": _url("staff_staffmember_changelist"),
                },
                {
                    "label": _("Pending invitations"),
                    "value": StaffInvitation.objects.filter(restaurant=restaurant, status="pending").count(),
                    "url": _url("staff_staffinvitation_changelist"),
                },
            ],
            [
                _link(_("Members"), "staff_staffmember_changelist"),
                _link(_("Invite"), "staff_staffinvitation_add"),
                _link(_("Roles"), "staff_staffrole_changelist"),
            ],
            "staff",
        )

    if has_resource_permission(request, "settings", "read"):
        card(
            None,
            "Settings",
            "settings",
            [{"label": _("Modules on"), "value": len(on), "url": _url("tenants_restaurantmodules_changelist")}],
            [
                _link(_("Restaurant settings"), "tenants_restaurant_changelist"),
                _link(_("Modules"), "tenants_restaurantmodules_changelist"),
            ],
            "settings",
        )
    return cards
