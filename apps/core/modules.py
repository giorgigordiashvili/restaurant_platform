"""
Modules: the product areas a restaurant can switch on or off.

Each module maps to a boolean on ``Restaurant``; the registry below is the
single description of what a module contains (sidebar apps, admin models,
staff-permission resources, dependencies, side-effect hooks). Everything that
adapts to a restaurant's configuration -- the tenant admin sidebar, the
Modules page, the dashboard, the role editor, the public API's ``modules``
field, the POS tabs -- reads this registry rather than the flags directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class ModuleError(ValidationError):
    """A module cannot be switched because of a dependency or an option rule."""


@dataclass(frozen=True)
class Module:
    code: str
    title: str
    description: str
    icon: str  # Material Symbols name (Unfold sidebar / cards)
    flag: str | None  # Restaurant boolean; None when derived from sub_flags (payments) or always on
    sub_flags: tuple[str, ...] = ()  # extra booleans edited on the module card
    sub_fields: tuple[str, ...] = ()  # extra text fields edited on the module card
    requires: tuple[str, ...] = ()  # hard dependencies (module codes)
    recommends: tuple[str, ...] = ()  # soft dependencies: warning only
    app_labels: tuple[str, ...] = ()  # admin apps hidden when the module is off
    model_names: tuple[tuple[str, str], ...] = ()  # (app_label, model_name) in sidebar order
    resources: tuple[str, ...] = ()  # StaffRole permission resources the module unlocks
    hooks: tuple[str, ...] = ()  # dotted paths: fn(restaurant, enabled, *, by=None)
    always_on: bool = False

    @property
    def switchable(self) -> bool:
        return not self.always_on and self.flag is not None


MODULES: tuple[Module, ...] = (
    Module(
        "menu",
        _("Menu"),
        _("Categories, dishes and modifiers. The QR menu customers browse."),
        "restaurant_menu",
        None,
        always_on=True,
        app_labels=("menu",),
        model_names=(("menu", "menucategory"), ("menu", "menuitem"), ("menu", "modifiergroup"), ("menu", "modifier")),
        resources=("menu",),
    ),
    Module(
        "ordering",
        _("Ordering"),
        _("Customers order from the menu: QR dine-in, takeaway. Off = menu only."),
        "receipt_long",
        "accepts_remote_orders",
        sub_flags=("accepts_takeaway",),
        app_labels=("orders",),
        model_names=(("orders", "order"),),
        resources=("orders",),
    ),
    Module(
        "tables",
        _("Tables & QR"),
        _("Sections, tables, QR codes, table sessions and shared venues."),
        "table_restaurant",
        "tables_enabled",
        app_labels=("tables", "venues"),
        model_names=(
            ("tables", "tablesection"),
            ("tables", "table"),
            ("tables", "tableqrcode"),
            ("tables", "tablesession"),
            ("venues", "venuesharerequest"),
        ),
        resources=("tables",),
    ),
    Module(
        "reservations",
        _("Reservations"),
        _("Online booking with deposits, blocked times and reservation rules."),
        "event_seat",
        "accepts_reservations",
        app_labels=("reservations",),
        model_names=(
            ("reservations", "reservation"),
            ("reservations", "reservationsettings"),
            ("reservations", "reservationblockedtime"),
        ),
        resources=("reservations",),
        hooks=("apps.reservations.hooks.on_module_toggled",),
    ),
    Module(
        "kitchen",
        _("Kitchen display"),
        _("The Kitchen screen in the POS app: cooks accept, cook and bump tickets."),
        "skillet",
        "kitchen_enabled",
        requires=("ordering",),
    ),
    Module(
        "warehouse",
        _("Warehouse"),
        _("Stock in lots, recipes on dishes, automatic sold-out, buy lists, delivery-platform checklists."),
        "inventory_2",
        "warehouse_enabled",
        recommends=("ordering",),
        app_labels=("inventory",),
        model_names=(
            ("inventory", "warehouseoverview"),
            ("inventory", "stockitem"),
            ("inventory", "stocklot"),
            ("inventory", "wasteentry"),
            ("inventory", "employeemeal"),
            ("inventory", "stockadjustment"),
            ("inventory", "stockmovement"),
            ("inventory", "inventoryalert"),
        ),
        resources=("warehouse", "warehouse_logs"),
        hooks=("apps.inventory.hooks.on_feature_toggled",),
    ),
    Module(
        "loyalty",
        _("Loyalty"),
        _("Punch-card programs redeemed in the POS; optionally the platform-wide tier discounts."),
        "loyalty",
        "loyalty_enabled",
        sub_flags=("accepts_platform_loyalty",),
        app_labels=("loyalty",),
        model_names=(("loyalty", "loyaltyprogram"), ("loyalty", "loyaltycounter"), ("loyalty", "loyaltyredemption")),
        resources=("menu",),
    ),
    Module(
        "reviews",
        _("Reviews"),
        _("Customer reviews on your page, with reporting to the platform moderators."),
        "reviews",
        "reviews_enabled",
        app_labels=("reviews",),
        model_names=(("reviews", "review"),),
        resources=("menu",),
    ),
    Module(
        "printing",
        _("Printing"),
        _(
            "Kitchen and bar ticket printers, receipt printers and cash drawer through the print bridge (any cheap ESC/POS printer)."
        ),
        "print",
        "printing_enabled",
        recommends=("ordering",),
        app_labels=("printing",),
        model_names=(("printing", "printer"), ("printing", "printjob")),
        resources=("settings", "orders"),
        hooks=("apps.printing.hooks.on_module_toggled",),
    ),
    Module(
        "cash",
        _("Cash & payments"),
        _("Cash shifts with X/Z reports, taking payments in the POS, discounts, comps, voids and refunds."),
        "point_of_sale",
        "cash_enabled",
        requires=("ordering",),
        app_labels=("payments",),
        model_names=(("payments", "cashshift"), ("payments", "payment"), ("payments", "discountreason")),
        resources=("cash",),
    ),
    Module(
        "delivery",
        _("Delivery platforms"),
        _(
            "Glovo and Wolt orders straight into the kitchen, menu push, sold-out sync, store pause; Bolt Food coming soon."
        ),
        "delivery_dining",
        "delivery_enabled",
        requires=("ordering",),
        recommends=("kitchen", "warehouse"),
        app_labels=("delivery",),
        model_names=(("delivery", "deliveryplatformspage"),),
        resources=("orders", "settings"),
        hooks=("apps.delivery.hooks.on_module_toggled",),
    ),
    Module(
        "waitlist",
        _("Waitlist & walk-ins"),
        _(
            "Today's queue at the door: quoted waits, 'table ready' SMS, seat straight into a table, guests join from a QR."
        ),
        "hourglass_top",
        "waitlist_enabled",
        requires=("tables",),
        recommends=("reservations", "notifications"),
        app_labels=("waitlist",),
        model_names=(("waitlist", "waitlistentry"), ("waitlist", "waitlistsettingspage")),
        resources=("reservations",),
        hooks=("apps.waitlist.hooks.on_module_toggled",),
    ),
    Module(
        "terminals",
        _("Card terminals & pay-by-link"),
        _(
            "Card payments the POS can follow: cashier confirmation on a physical terminal, Bank of Georgia / TBC pay-by-link with a QR at the table, ECR terminals through the terminal bridge."
        ),
        "contactless",
        "terminals_enabled",
        requires=("cash",),
        app_labels=("terminals",),
        model_names=(
            ("terminals", "paymentterminal"),
            ("terminals", "terminaltransaction"),
            ("terminals", "terminalreconciliation"),
        ),
        resources=("cash",),
        hooks=("apps.terminals.hooks.on_module_toggled",),
    ),
    Module(
        "online_ordering",
        _("Online ordering & delivery"),
        _(
            "Pickup and delivery from your own page: opening hours, time slots, delivery zones with fees, own couriers or a Wolt Drive / Glovo courier, custom domain."
        ),
        "storefront",
        "online_ordering_enabled",
        requires=("ordering",),
        recommends=("notifications", "payments"),
        app_labels=("ordering",),
        model_names=(
            ("ordering", "onlineorderingsettingspage"),
            ("ordering", "deliveryzone"),
            ("ordering", "courier"),
            ("ordering", "delivery"),
            ("ordering", "restaurantdomain"),
        ),
        resources=("orders", "settings"),
        hooks=("apps.ordering.hooks.on_module_toggled",),
    ),
    Module(
        "fiscal",
        _("Fiscal & VAT"),
        _(
            "VAT profile, sequential receipts and refund receipts, RS.ge waybill export. A live fiscal provider plugs in later."
        ),
        "receipt",
        "fiscal_enabled",
        recommends=("ordering",),
        app_labels=("fiscal",),
        model_names=(("fiscal", "fiscalsettingspage"), ("fiscal", "fiscaldocument")),
        resources=("fiscal",),
        hooks=("apps.fiscal.hooks.on_module_toggled",),
    ),
    Module(
        "crm",
        _("CRM & marketing"),
        _(
            "Guest records built from orders and bookings, segments, SMS / email campaigns, birthday, review and win-back automations."
        ),
        "campaign",
        "crm_enabled",
        requires=("ordering",),
        recommends=("notifications", "loyalty", "promotions"),
        app_labels=("crm",),
        model_names=(("crm", "customer"), ("crm", "campaign"), ("crm", "segment"), ("crm", "automation")),
        resources=("crm",),
        hooks=("apps.crm.hooks.on_module_toggled",),
    ),
    Module(
        "timekeeping",
        _("Timekeeping & rota"),
        _("Clock in / out on the POS, weekly rota with reminders, hours and labour cost report."),
        "schedule",
        "timekeeping_enabled",
        app_labels=("timekeeping",),
        model_names=(("timekeeping", "rotashift"), ("timekeeping", "timeentry")),
        resources=("timekeeping",),
        hooks=("apps.timekeeping.hooks.on_module_toggled",),
    ),
    Module(
        "purchasing",
        _("Purchasing"),
        _("Suppliers with price lists, purchase orders straight from the buy list, receiving against the order."),
        "local_shipping",
        "purchasing_enabled",
        requires=("warehouse",),
        app_labels=("purchasing",),
        model_names=(("purchasing", "purchaseorder"), ("purchasing", "supplier")),
        hooks=("apps.purchasing.hooks.on_module_toggled",),
    ),
    Module(
        "promotions",
        _("Promotions & schedules"),
        _("Breakfast / lunch menus by time, '86 today', happy hours, promo codes and combo menus."),
        "sell",
        "promotions_enabled",
        requires=("menu",),
        app_labels=("promotions",),
        model_names=(("promotions", "promotion"), ("promotions", "menuschedule")),
        hooks=("apps.promotions.hooks.on_module_toggled",),
    ),
    Module(
        "notifications",
        _("Notifications"),
        _(
            "Staff push and in-app alerts (new orders, reservations, sold-out, printer failures, Z-reports); guest SMS / email for reservations."
        ),
        "notifications_active",
        "notifications_enabled",
        app_labels=("notifications",),
        model_names=(("notifications", "notificationsettingspage"), ("notifications", "outboundmessage")),
        hooks=("apps.notifications.hooks.on_module_toggled",),
    ),
    Module(
        "payments",
        _("Online payments"),
        _("Card payments via Bank of Georgia and/or Flitt. Without them customers pay cash at the table."),
        "credit_card",
        None,
        sub_flags=("accepts_bog_payments", "accepts_flitt_payments"),
        sub_fields=("bog_payout_iban", "flitt_sub_merchant_id"),
        recommends=("ordering",),
    ),
)
MODULES_BY_CODE = {m.code: m for m in MODULES}
CODES = tuple(m.code for m in MODULES)

# Resources every restaurant has regardless of modules.
BASE_RESOURCES = ("staff", "settings", "analytics")


def get(code: str) -> Module:
    try:
        return MODULES_BY_CODE[code]
    except KeyError:
        raise ModuleError(f"Unknown module '{code}'.")


def is_enabled(restaurant, code: str) -> bool:
    m = get(code)
    if m.always_on:
        return True
    if m.flag is None:
        return any(getattr(restaurant, f, False) for f in m.sub_flags)
    return bool(getattr(restaurant, m.flag, False))


def enabled_modules(restaurant) -> list[Module]:
    return [m for m in MODULES if is_enabled(restaurant, m.code)]


def enabled_codes(restaurant) -> set[str]:
    return {m.code for m in enabled_modules(restaurant)}


def modules_dict(restaurant) -> dict[str, bool]:
    return {m.code: is_enabled(restaurant, m.code) for m in MODULES}


def hidden_apps(restaurant) -> set[str]:
    """App labels that belong only to disabled modules."""
    on = enabled_codes(restaurant)
    used = {label for m in MODULES if m.code in on for label in m.app_labels}
    return {label for m in MODULES if m.code not in on for label in m.app_labels} - used


def hidden_models(restaurant) -> set[tuple[str, str]]:
    on = enabled_codes(restaurant)
    return {name for m in MODULES if m.code not in on for name in m.model_names}


def resources_available(restaurant) -> list[str]:
    """Permission resources a role editor should offer, in module order."""
    out: list[str] = []
    for m in enabled_modules(restaurant):
        for r in m.resources:
            if r not in out:
                out.append(r)
    for r in BASE_RESOURCES:
        if r not in out:
            out.append(r)
    return out


def dependents(code: str) -> list[Module]:
    return [m for m in MODULES if code in m.requires]


def check_dependencies(restaurant, code: str, enabled: bool) -> list[str]:
    """Reasons the switch would be refused (empty = fine)."""
    m = get(code)
    if enabled:
        return [f"{m.title} needs {get(r).title} to be on." for r in m.requires if not is_enabled(restaurant, r)]
    return [
        f"Turn off {d.title} first -- it needs {m.title}." for d in dependents(code) if is_enabled(restaurant, d.code)
    ]


def warnings(restaurant, code: str) -> list[str]:
    m = get(code)
    return [
        f"{m.title} works best together with {get(r).title}." for r in m.recommends if not is_enabled(restaurant, r)
    ]


def set_module(restaurant, code: str, enabled: bool, *, by=None) -> bool:
    """
    Switch one module. Validates dependencies, saves the flag, runs the
    module's hooks and writes an audit row. Returns False when nothing changed.
    """
    m = get(code)
    if not m.switchable:
        raise ModuleError(f"{m.title} cannot be switched on or off.")
    problems = check_dependencies(restaurant, code, enabled)
    if problems:
        raise ModuleError(" ".join(problems))
    if bool(getattr(restaurant, m.flag)) == enabled:
        return False
    setattr(restaurant, m.flag, enabled)
    restaurant.save(update_fields=[m.flag, "updated_at"])
    for path in m.hooks:
        try:
            import_string(path)(restaurant, enabled, by=by)
        except Exception:  # a hook must never undo the switch
            logger.exception("module hook %s failed for %s", path, restaurant.slug)
    _audit(restaurant, by, f"Module '{m.title}' {'enabled' if enabled else 'disabled'}", {m.flag: enabled})
    return True


def set_options(restaurant, code: str, data: dict, *, by=None) -> None:
    """Save a module's sub-flags / sub-fields, validated through Restaurant.clean()."""
    m = get(code)
    fields = [*m.sub_flags, *m.sub_fields]
    changed = {}
    for name in fields:
        if name in data and getattr(restaurant, name) != data[name]:
            setattr(restaurant, name, data[name])
            changed[name] = data[name]
    if not changed:
        return
    restaurant.full_clean(
        exclude=[f.name for f in restaurant._meta.fields if f.name not in fields], validate_unique=False
    )
    restaurant.save(update_fields=[*changed, "updated_at"])
    _audit(restaurant, by, f"{m.title} options updated", changed)


def apply_flags(restaurant, flags: dict, *, by=None) -> None:
    """
    Apply several module flags at once (dashboard settings API). Turning
    modules off is processed before turning on so dependency checks see the
    final picture.
    """
    by_flag = {m.flag: m for m in MODULES if m.flag}
    wanted = {by_flag[name].code: bool(enabled) for name, enabled in flags.items() if name in by_flag}
    # Dependants come after their requirements in the registry: disable in
    # reverse order (kitchen before ordering), enable in registry order.
    for m in reversed(MODULES):
        if wanted.get(m.code) is False:
            set_module(restaurant, m.code, False, by=by)
    for m in MODULES:
        if wanted.get(m.code) is True:
            set_module(restaurant, m.code, True, by=by)


def _audit(restaurant, by, description, changes):
    try:
        from apps.audit.services import log_action

        log_action(
            "settings_update",
            user=by if getattr(by, "is_authenticated", False) else None,
            restaurant=restaurant,
            description=description,
            target_model="Restaurant",
            target_id=str(restaurant.pk),
            changes=changes,
        )
    except Exception:  # pragma: no cover - auditing must not break the switch
        logger.exception("module audit failed")
