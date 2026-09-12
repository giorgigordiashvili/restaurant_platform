"""
Tenant-admin sidebar, built per request from the module registry.

Unfold calls ``navigation(request)`` on every admin page; each item carries a
``permission`` callable so a role without read access to a resource never
sees the link, and a group whose items are all hidden collapses entirely.
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from apps.core import modules
from apps.core.tenant_admin_base import has_resource_permission

ICONS = {
    "menucategory": "category",
    "menuitem": "restaurant_menu",
    "modifiergroup": "tune",
    "modifier": "toggle_on",
    "order": "receipt_long",
    "tablesection": "dashboard",
    "table": "table_restaurant",
    "tableqrcode": "qr_code_2",
    "tablesession": "groups",
    "venuesharerequest": "storefront",
    "reservation": "event_seat",
    "reservationsettings": "settings_suggest",
    "reservationblockedtime": "event_busy",
    "warehouseoverview": "dashboard",
    "stockitem": "inventory_2",
    "stocklot": "local_shipping",
    "wasteentry": "delete_sweep",
    "employeemeal": "lunch_dining",
    "stockadjustment": "fact_check",
    "stockmovement": "history",
    "inventoryalert": "notifications",
    "loyaltyprogram": "loyalty",
    "loyaltycounter": "counter_1",
    "loyaltyredemption": "redeem",
    "review": "reviews",
    "cashshift": "point_of_sale",
    "payment": "payments",
    "discountreason": "sell",
    "deliveryplatformspage": "delivery_dining",
    "promotion": "sell",
    "menuschedule": "schedule",
    "notificationsettingspage": "notifications_active",
    "outboundmessage": "outgoing_mail",
    "fiscalsettingspage": "receipt",
    "fiscaldocument": "receipt_long",
    "printer": "print",
    "printjob": "print_connect",
    "salesreport": "monitoring",
    "menureport": "restaurant_menu",
    "foodcostreport": "savings",
    "staffreport": "groups",
    "shiftsreport": "point_of_sale",
    "reservationsreport": "event_seat",
    "reviewsreport": "reviews",
    "staffmember": "badge",
    "staffinvitation": "mail",
    "staffrole": "admin_panel_settings",
    "restaurant": "storefront",
    "restaurantmodules": "extension",
}


def _perm(resource):
    def check(request):
        return has_resource_permission(request, resource, "read")

    return check


def _model_item(site, app_label, model_name, title=None):
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return None
    if model not in site._registry:
        return None
    try:
        link = reverse(f"{site.name}:{app_label}_{model_name}_changelist")
    except NoReverseMatch:
        return None
    return {
        "title": title or str(model._meta.verbose_name_plural).capitalize(),
        "icon": ICONS.get(model_name, "chevron_right"),
        "link": link,
        "permission": _perm(site.MODEL_TO_RESOURCE.get(model_name)),
    }


def navigation(request):
    from apps.core.admin_sites import tenant_admin_site as site

    restaurant = getattr(request, "restaurant", None)
    if not restaurant or not getattr(request.user, "is_authenticated", False):
        return []

    groups = [{"items": [{"title": _("Dashboard"), "icon": "dashboard", "link": reverse(f"{site.name}:index")}]}]
    for m in modules.enabled_modules(restaurant):
        items = [_model_item(site, app, name) for app, name in m.model_names]
        if m.code == "kitchen":
            items.append(
                {
                    "title": _("Kitchen screen (POS)"),
                    "icon": "skillet",
                    "link": f"{settings.POS_BASE_URL}/kitchen",
                    "permission": _perm("orders"),
                }
            )
        items = [i for i in items if i]
        if items:
            groups.append({"title": m.title, "separator": True, "items": items})

    if has_resource_permission(request, "analytics", "read"):
        from apps.reports.tenant_admin import REPORTS

        items = [
            _model_item(site, "reports", model._meta.model_name, title)
            for _key, title, model, module in REPORTS
            if not module or modules.is_enabled(restaurant, module)
        ]
        items = [i for i in items if i]
        if items:
            groups.append({"title": _("Reports"), "separator": True, "items": items})

    groups.append(
        {
            "title": _("Staff"),
            "separator": True,
            "items": [
                i
                for i in (
                    _model_item(site, "staff", "staffmember", _("Members")),
                    _model_item(site, "staff", "staffinvitation", _("Invitations")),
                    _model_item(site, "staff", "staffrole", _("Roles")),
                )
                if i
            ],
        }
    )
    groups.append(
        {
            "title": _("Settings"),
            "separator": True,
            "items": [
                i
                for i in (
                    _model_item(site, "tenants", "restaurant", _("Restaurant settings")),
                    _model_item(site, "tenants", "restaurantmodules", _("Modules")),
                )
                if i
            ],
        }
    )
    return groups
