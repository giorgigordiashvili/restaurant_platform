"""
First-login setup wizard for a restaurant's owner.

The wizard is a linear list of steps kept in ``Restaurant.setup_state``:

    welcome  -> which modules do you need? (checklist)
    details  -> name, category, contact, address, locale
    branding -> logo, cover, brand colour
    hours    -> opening hours
    module:<code> x N -> one step per selected module: "set it up" or "skip"
    done     -> summary and links

Every module decision goes through ``apps.core.modules.set_module`` so the
dependency rules, hooks and the audit trail are the same as on the Modules
page. Nothing here is reachable through the public API; the tenant admin
(``apps.tenants.setup_admin``) is the only client.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core import modules

# The checklist on the welcome step, in display order. Modules missing from
# every group are appended under "Other" so a new module is never invisible.
GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("front", _("Front of house"), ("ordering", "tables", "reservations", "waitlist", "kitchen", "printing")),
    ("money", _("Money"), ("cash", "terminals", "gift_cards", "house_accounts", "fiscal")),
    (
        "guests",
        _("Guests & marketing"),
        ("online_ordering", "delivery", "reviews", "loyalty", "promotions", "crm", "notifications"),
    ),
    ("back", _("Back office"), ("warehouse", "purchasing", "timekeeping")),
)

# Pre-checked on a fresh restaurant (on top of whatever is already on).
DEFAULT_SELECTED = ("ordering", "tables", "reservations")

# Where "Set it up" lands, per module: the page where the owner configures it.
# Missing = the module's first admin model; None = nothing to configure in
# the admin (the module lives in the POS), so the step only switches it on.
LANDING: dict[str, str | None] = {
    "ordering": "menu_menuitem_changelist",
    "kitchen": None,
    "reservations": "reservations_reservationsettings_changelist",
    "cash": "payments_discountreason_changelist",
    "loyalty": "loyalty_loyaltyprogram_add",
    "terminals": "terminals_paymentterminal_add",
    "printing": "printing_printer_add",
}

BASE_STEPS = ("welcome", "details", "branding", "hours")


@dataclass(frozen=True)
class Step:
    key: str
    kind: str
    title: str
    module: modules.Module | None = None


def state(restaurant) -> dict:
    return dict(restaurant.setup_state or {})


def save_state(restaurant, **changes) -> dict:
    st = state(restaurant)
    st.update(changes)
    restaurant.setup_state = st
    restaurant.save(update_fields=["setup_state", "updated_at"])
    return st


def is_pending(restaurant) -> bool:
    """The dashboard should send this restaurant to the wizard."""
    return restaurant.setup_completed_at is None


def is_deferred(restaurant) -> bool:
    """Wizard put aside with "finish later": completed for routing, still shown on the dashboard."""
    st = state(restaurant)
    return bool(st.get("deferred")) and not st.get("finished_at")


def switchable() -> list[modules.Module]:
    return [m for m in modules.MODULES if m.switchable]


def selected_codes(restaurant) -> list[str]:
    st = state(restaurant)
    if "selected" in st:
        return [c for c in st["selected"] if c in modules.MODULES_BY_CODE]
    return [m.code for m in switchable() if modules.is_enabled(restaurant, m.code) or m.code in DEFAULT_SELECTED]


def with_requirements(codes) -> list[str]:
    """The codes plus everything they require, in registry order."""
    wanted = set()

    def add(code):
        if code in wanted or code not in modules.MODULES_BY_CODE:
            return
        wanted.add(code)
        for r in modules.MODULES_BY_CODE[code].requires:
            add(r)

    for c in codes:
        add(c)
    return [m.code for m in switchable() if m.code in wanted]


def groups(restaurant) -> list[dict]:
    """Welcome-step checklist: [{key, title, modules: [{module, checked}]}]."""
    chosen = set(selected_codes(restaurant))
    placed = set()
    out = []
    for key, title, codes in GROUPS:
        items = []
        for code in codes:
            m = modules.MODULES_BY_CODE.get(code)
            if m is None or not m.switchable:
                continue
            placed.add(code)
            items.append({"module": m, "checked": code in chosen})
        if items:
            out.append({"key": key, "title": title, "modules": items})
    rest = [{"module": m, "checked": m.code in chosen} for m in switchable() if m.code not in placed]
    if rest:
        out.append({"key": "other", "title": _("Other"), "modules": rest})
    return out


def steps(restaurant) -> list[Step]:
    titles = {
        "welcome": _("What do you need?"),
        "details": _("Restaurant details"),
        "branding": _("Logo & look"),
        "hours": _("Opening hours"),
    }
    out = [Step(k, k, titles[k]) for k in BASE_STEPS]
    chosen = set(selected_codes(restaurant))
    out += [Step(f"module:{m.code}", "module", m.title, m) for m in switchable() if m.code in chosen]
    out.append(Step("done", "done", _("All set")))
    return out


def get_step(restaurant, key: str | None) -> Step:
    all_steps = steps(restaurant)
    if key:
        for s in all_steps:
            if s.key == key:
                return s
    current = state(restaurant).get("current")
    for s in all_steps:
        if s.key == current:
            return s
    return all_steps[0]


def next_step(restaurant, key: str) -> Step:
    all_steps = steps(restaurant)
    for i, s in enumerate(all_steps):
        if s.key == key:
            return all_steps[min(i + 1, len(all_steps) - 1)]
    return all_steps[0]


def progress(restaurant, key: str) -> dict:
    all_steps = steps(restaurant)
    index = next((i for i, s in enumerate(all_steps) if s.key == key), 0)
    return {"index": index + 1, "total": len(all_steps), "percent": int(100 * index / max(len(all_steps) - 1, 1))}


def advance(restaurant, key: str) -> Step:
    """Mark ``key`` done and make the following step current."""
    st = state(restaurant)
    done = [k for k in st.get("done_steps", []) if k != key] + [key]
    nxt = next_step(restaurant, key)
    save_state(restaurant, done_steps=done, current=nxt.key)
    return nxt


def apply_selection(restaurant, codes, *, by=None) -> list[str]:
    """
    Welcome step: remember what the owner wants to be asked about. Modules
    that are on but were not ticked go off right away (dependants first);
    ticked ones stay as they are until their own step decides.
    """
    chosen = with_requirements(codes)
    wanted = set(chosen)
    for m in reversed(switchable()):
        if m.code not in wanted and modules.is_enabled(restaurant, m.code):
            try:
                modules.set_module(restaurant, m.code, False, by=by)
            except modules.ModuleError:
                pass
    decisions = {k: v for k, v in state(restaurant).get("decisions", {}).items() if k in wanted}
    save_state(restaurant, selected=chosen, decisions=decisions)
    return chosen


def enable_with_requirements(restaurant, code: str, *, by=None) -> list[str]:
    """Switch ``code`` on, turning on what it requires first. Returns what was switched on."""
    switched = []
    for c in with_requirements([code]):
        if not modules.is_enabled(restaurant, c):
            modules.set_module(restaurant, c, True, by=by)
            switched.append(c)
    return switched


def decide(restaurant, code: str, decision: str, *, by=None) -> list[str]:
    """Module step. ``enabled`` switches the module on, ``skipped`` leaves it off."""
    m = modules.get(code)
    switched: list[str] = []
    if decision == "enabled":
        switched = enable_with_requirements(restaurant, code, by=by)
    elif modules.is_enabled(restaurant, code) and m.switchable:
        try:
            modules.set_module(restaurant, code, False, by=by)
        except modules.ModuleError:
            pass  # a dependant that was set up earlier keeps it on
    decisions = state(restaurant).get("decisions", {})
    decisions[code] = decision
    save_state(restaurant, decisions=decisions)
    return switched


def landing_url(code: str) -> str | None:
    m = modules.MODULES_BY_CODE.get(code)
    if m is None:
        return None
    name = LANDING.get(code, f"{m.model_names[0][0]}_{m.model_names[0][1]}_changelist" if m.model_names else None)
    if not name:
        return None
    try:
        return reverse(f"tenant_admin:{name}")
    except NoReverseMatch:
        return None


def finish(restaurant) -> None:
    now = timezone.now()
    restaurant.setup_completed_at = now
    restaurant.save(update_fields=["setup_completed_at", "updated_at"])
    save_state(restaurant, current="done", finished_at=now.isoformat(), deferred=False)


def defer(restaurant) -> None:
    """ "Finish later": stop redirecting the dashboard, keep the progress."""
    restaurant.setup_completed_at = timezone.now()
    restaurant.save(update_fields=["setup_completed_at", "updated_at"])
    save_state(restaurant, deferred=True)


def restart(restaurant) -> None:
    restaurant.setup_completed_at = None
    restaurant.setup_state = {}
    restaurant.save(update_fields=["setup_completed_at", "setup_state", "updated_at"])


def summary(restaurant) -> dict:
    decisions = state(restaurant).get("decisions", {})
    on = [m for m in switchable() if modules.is_enabled(restaurant, m.code)]
    skipped = [
        m for m in switchable() if decisions.get(m.code) == "skipped" and not modules.is_enabled(restaurant, m.code)
    ]
    return {"enabled": on, "skipped": skipped}
