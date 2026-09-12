"""Is this dish on the menu right now? Schedules, '86 today' and category schedules, in the restaurant's timezone."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.reports.periods import restaurant_tz


def local_now(restaurant, now=None):
    return (now or timezone.now()).astimezone(restaurant_tz(restaurant))


def end_of_day(restaurant, now=None):
    """Midnight tonight in the restaurant's timezone (as an aware datetime)."""
    local = local_now(restaurant, now)
    tomorrow = (local + timedelta(days=1)).date()
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=local.tzinfo)


def availability(item, *, now=None, restaurant=None) -> tuple[bool, str]:
    """(available_now, reason_text) — reason is human-readable ('until tomorrow', '12:00–16:00 (Mon, Tue)')."""
    restaurant = restaurant or item.restaurant
    local = local_now(restaurant, now)
    until = getattr(item, "unavailable_until", None)
    if until and until > (now or timezone.now()):
        return False, _("Not available today")
    schedule = getattr(item, "schedule", None)
    if schedule is not None and not schedule.matches(local):
        return False, schedule.label()
    category = getattr(item, "category", None)
    cat_schedule = getattr(category, "schedule", None) if category is not None else None
    if cat_schedule is not None and not cat_schedule.matches(local):
        return False, cat_schedule.label()
    return True, ""


def is_available_now(item, *, now=None, restaurant=None) -> bool:
    return availability(item, now=now, restaurant=restaurant)[0]
