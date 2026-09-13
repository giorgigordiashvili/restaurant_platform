"""
Opening hours in the restaurant's own timezone.

``Restaurant.is_open_now`` used to compare the server's local time with one
open/close pair per weekday, which ignored ``restaurant.timezone`` and broke
for venues that close after midnight. Everything that needs "are we open"
(the public page, online ordering, pickup slots) goes through here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone


@dataclass(frozen=True)
class Interval:
    start: datetime  # aware, restaurant tz
    end: datetime  # aware, restaurant tz; > start (next day when closing past midnight)

    def contains(self, dt: datetime) -> bool:
        return self.start <= dt < self.end


def tz(restaurant) -> ZoneInfo:
    try:
        return ZoneInfo(getattr(restaurant, "timezone", "") or "Asia/Tbilisi")
    except Exception:  # noqa: BLE001 - unknown zone name
        return ZoneInfo("Asia/Tbilisi")


def local_now(restaurant, now: datetime | None = None) -> datetime:
    now = now or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now)
    return now.astimezone(tz(restaurant))


def _hours_by_day(restaurant) -> dict[int, object]:
    rows = getattr(restaurant, "_hours_cache", None)
    if rows is None:
        rows = {h.day_of_week: h for h in restaurant.operating_hours.all()}
        restaurant._hours_cache = rows
    return rows


def _pairs(hours) -> list[tuple[time, time]]:
    if hours is None or hours.is_closed:
        return []
    out = [(hours.open_time, hours.close_time)]
    second_open = getattr(hours, "open_time_2", None)
    second_close = getattr(hours, "close_time_2", None)
    if second_open and second_close:
        out.append((second_open, second_close))
    return out


def intervals_for(restaurant, day: date) -> list[Interval]:
    """Service intervals that *start* on ``day`` (a 22:00-02:00 shift belongs to the day it opens)."""
    zone = tz(restaurant)
    hours = _hours_by_day(restaurant).get(day.weekday())
    out: list[Interval] = []
    for open_t, close_t in _pairs(hours):
        start = datetime.combine(day, open_t, tzinfo=zone)
        end = datetime.combine(day, close_t, tzinfo=zone)
        if end <= start:
            end += timedelta(days=1)
        out.append(Interval(start, end))
    return out


def interval_at(restaurant, dt: datetime | None = None) -> Interval | None:
    """The interval covering ``dt`` (default now), looking at today's and yesterday's shifts."""
    now = local_now(restaurant, dt)
    for day in (now.date() - timedelta(days=1), now.date()):
        for iv in intervals_for(restaurant, day):
            if iv.contains(now):
                return iv
    return None


def is_open_at(restaurant, dt: datetime | None = None) -> bool:
    return interval_at(restaurant, dt) is not None


def next_opening(restaurant, dt: datetime | None = None, *, days: int = 8) -> datetime | None:
    """First interval start after ``dt`` (None when the restaurant never opens in the window)."""
    now = local_now(restaurant, dt)
    for offset in range(days):
        day = now.date() + timedelta(days=offset)
        for iv in intervals_for(restaurant, day):
            if iv.start > now:
                return iv.start
    return None


def closes_at(restaurant, dt: datetime | None = None) -> datetime | None:
    iv = interval_at(restaurant, dt)
    return iv.end if iv else None
