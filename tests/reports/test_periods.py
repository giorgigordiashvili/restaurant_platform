from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from apps.reports.periods import Period, parse_period


class Restaurant:
    timezone = "Asia/Tbilisi"


NOW = datetime(2026, 9, 12, 21, 30, tzinfo=ZoneInfo("UTC"))  # 01:30 on the 13th in Tbilisi


def test_today_is_the_restaurants_today():
    p = parse_period({}, Restaurant(), now=NOW)
    assert p.key == "today" and p.start_date == date(2026, 9, 13)
    assert p.start.isoformat() == "2026-09-13T00:00:00+04:00"
    assert p.end.isoformat() == "2026-09-14T00:00:00+04:00"


@pytest.mark.parametrize(
    "key,start,end",
    [
        ("yesterday", date(2026, 9, 12), date(2026, 9, 12)),
        ("week", date(2026, 9, 7), date(2026, 9, 13)),  # Monday-start
        ("month", date(2026, 9, 1), date(2026, 9, 13)),
        ("last7", date(2026, 9, 7), date(2026, 9, 13)),
        ("last30", date(2026, 8, 15), date(2026, 9, 13)),
    ],
)
def test_named_ranges(key, start, end):
    p = parse_period({"range": key}, Restaurant(), now=NOW)
    assert (p.start_date, p.end_date) == (start, end)


def test_custom_swaps_clamps_and_falls_back():
    p = parse_period({"range": "custom", "from": "2026-09-10", "to": "2026-09-01"}, Restaurant(), now=NOW)
    assert (p.start_date, p.end_date) == (date(2026, 9, 1), date(2026, 9, 10))
    p = parse_period({"range": "custom", "from": "2020-01-01", "to": "2026-09-01"}, Restaurant(), now=NOW)
    assert p.days == 366
    p = parse_period({"range": "custom", "from": "garbage"}, Restaurant(), now=NOW)
    assert p.key == "today"
    p = parse_period({"range": "nope"}, Restaurant(), now=NOW)
    assert p.key == "today"


def test_previous_period():
    tz = ZoneInfo("Asia/Tbilisi")
    week = Period("week", date(2026, 9, 7), date(2026, 9, 13), tz)
    assert (week.previous().start_date, week.previous().end_date) == (date(2026, 8, 31), date(2026, 9, 6))
    month = Period("month", date(2026, 3, 1), date(2026, 3, 30), tz)
    prev = month.previous()
    assert (prev.start_date, prev.end_date) == (date(2026, 2, 1), date(2026, 2, 28))  # capped at Feb's length
    day = Period("today", date(2026, 9, 13), date(2026, 9, 13), tz)
    assert day.previous().start_date == date(2026, 9, 12)
