"""
Report periods in the restaurant's own timezone.

``Restaurant.timezone`` was never honoured before; every day / hour boundary
here is computed in it, so "today" is the restaurant's today even though
the server clock is Asia/Tbilisi.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

MAX_DAYS = 366
RANGE_KEYS = ("today", "yesterday", "week", "month", "last7", "last30", "custom")


def restaurant_tz(restaurant) -> ZoneInfo:
    try:
        return ZoneInfo(getattr(restaurant, "timezone", None) or "Asia/Tbilisi")
    except Exception:  # unknown key in an old row
        return ZoneInfo("Asia/Tbilisi")


@dataclass(frozen=True)
class Period:
    key: str
    start_date: date
    end_date: date  # inclusive
    tz: ZoneInfo

    @property
    def start(self) -> datetime:
        return datetime.combine(self.start_date, datetime.min.time(), tzinfo=self.tz)

    @property
    def end(self) -> datetime:
        """Exclusive upper bound: midnight after ``end_date``."""
        return datetime.combine(self.end_date + timedelta(days=1), datetime.min.time(), tzinfo=self.tz)

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def label(self) -> str:
        if self.start_date == self.end_date:
            return self.start_date.isoformat()
        return f"{self.start_date.isoformat()} – {self.end_date.isoformat()}"

    def dates(self):
        d = self.start_date
        while d <= self.end_date:
            yield d
            d += timedelta(days=1)

    def previous(self) -> "Period":
        """The comparable period just before this one (calendar-aligned for months)."""
        if self.key == "month":
            first = self.start_date.replace(day=1)
            prev_last = first - timedelta(days=1)
            prev_first = prev_last.replace(day=1)
            # Same number of elapsed days into the previous month, capped at its length.
            elapsed = (self.end_date - first).days
            end = min(prev_first + timedelta(days=elapsed), prev_last)
            return Period("custom", prev_first, end, self.tz)
        length = self.days
        return Period("custom", self.start_date - timedelta(days=length), self.start_date - timedelta(days=1), self.tz)

    def query_string(self) -> str:
        if self.key == "custom":
            return f"range=custom&from={self.start_date.isoformat()}&to={self.end_date.isoformat()}"
        return f"range={self.key}"


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def parse_period(params, restaurant, now: datetime | None = None) -> Period:
    """
    ``?range=today|yesterday|week|month|last7|last30|custom&from=&to=``.
    Bad input falls back to today; custom ranges are swapped when reversed
    and capped at 366 days.
    """
    tz = restaurant_tz(restaurant)
    today = (now or timezone.now()).astimezone(tz).date()
    key = (params.get("range") or "today").lower()
    if key not in RANGE_KEYS:
        key = "today"
    if key == "today":
        return Period(key, today, today, tz)
    if key == "yesterday":
        y = today - timedelta(days=1)
        return Period(key, y, y, tz)
    if key == "week":
        return Period(key, today - timedelta(days=today.weekday()), today, tz)
    if key == "month":
        return Period(key, today.replace(day=1), today, tz)
    if key == "last7":
        return Period(key, today - timedelta(days=6), today, tz)
    if key == "last30":
        return Period(key, today - timedelta(days=29), today, tz)
    start = _parse_date(params.get("from"))
    end = _parse_date(params.get("to"))
    if start is None and end is None:
        return Period("today", today, today, tz)
    start = start or end
    end = end or start
    if start > end:
        start, end = end, start
    if (end - start).days >= MAX_DAYS:
        end = start + timedelta(days=MAX_DAYS - 1)
    return Period("custom", start, end, tz)
