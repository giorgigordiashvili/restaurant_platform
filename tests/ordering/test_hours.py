"""Timezone-aware opening hours: split shifts, past-midnight closing, next opening, slots."""

from datetime import date, datetime, time
from datetime import timezone as dt_tz
from zoneinfo import ZoneInfo

import pytest

from apps.ordering import services
from apps.tenants import hours as H
from apps.tenants.models import RestaurantHours

TBI = ZoneInfo("Asia/Tbilisi")


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=TBI)


@pytest.mark.django_db
class TestHours:
    def test_open_in_restaurant_timezone_not_server_time(self, ordering):
        # 2026-09-14 is a Monday. 11:00 Tbilisi = 07:00 UTC -> open; 09:00 Tbilisi -> closed.
        assert H.is_open_at(ordering, datetime(2026, 9, 14, 7, 0, tzinfo=dt_tz.utc))
        assert not H.is_open_at(ordering, at(2026, 9, 14, 9, 0))
        assert H.is_open_at(ordering, at(2026, 9, 14, 21, 59))
        assert not H.is_open_at(ordering, at(2026, 9, 14, 22, 0))

    def test_past_midnight_and_split_shift(self, ordering):
        RestaurantHours.objects.filter(restaurant=ordering, day_of_week=4).update(  # Friday
            open_time=time(12, 0), close_time=time(15, 0), open_time_2=time(18, 0), close_time_2=time(2, 0)
        )
        ordering._hours_cache = None
        assert H.is_open_at(ordering, at(2026, 9, 18, 13, 0))
        assert not H.is_open_at(ordering, at(2026, 9, 18, 16, 0))  # between services
        assert H.is_open_at(ordering, at(2026, 9, 18, 23, 30))
        assert H.is_open_at(ordering, at(2026, 9, 19, 1, 30))  # Saturday 01:30 still Friday's dinner
        assert H.closes_at(ordering, at(2026, 9, 19, 1, 30)) == at(2026, 9, 19, 2, 0)

    def test_closed_day_and_next_opening(self, ordering):
        RestaurantHours.objects.filter(restaurant=ordering, day_of_week=0).update(is_closed=True)
        ordering._hours_cache = None
        assert not H.is_open_at(ordering, at(2026, 9, 14, 12, 0))
        assert H.next_opening(ordering, at(2026, 9, 14, 12, 0)) == at(2026, 9, 15, 10, 0)
        assert H.next_opening(ordering, at(2026, 9, 15, 9, 0)) == at(2026, 9, 15, 10, 0)
        assert ordering.is_open_now in (True, False)  # property delegates without error

    def test_no_hours_means_closed(self, restaurant):
        assert not H.is_open_at(restaurant, at(2026, 9, 14, 12, 0))
        assert H.next_opening(restaurant, at(2026, 9, 14, 12, 0)) is None


@pytest.mark.django_db
class TestSlots:
    def test_slots_respect_lead_cutoff_and_interval(self, ordering):
        now = at(2026, 9, 14, 11, 7)
        slots = services.slots(ordering, "takeaway", date(2026, 9, 14), now=now)
        labels = [s["label"] for s in slots]
        assert labels[0] == "11:30"  # 11:07 + 20 min lead = 11:27 -> rounded up to 15-min grid
        assert labels[-1] == "21:30"  # 22:00 close - 30 min cutoff
        delivery = services.slots(ordering, "delivery", date(2026, 9, 14), now=now)
        assert delivery[0]["label"] == "11:45"  # + 10 extra minutes -> 11:37 -> 11:45

    def test_slots_future_day_and_limits(self, ordering):
        now = at(2026, 9, 14, 11, 0)
        tomorrow = services.slots(ordering, "takeaway", date(2026, 9, 15), now=now)
        assert tomorrow[0]["label"] == "10:00"
        assert services.slots(ordering, "takeaway", date(2026, 9, 13), now=now) == []
        assert services.slots(ordering, "takeaway", date(2026, 9, 30), now=now) == []
        cfg = services.settings_for(ordering)
        cfg.scheduling_enabled = False
        cfg.save()
        ordering._ordering_settings_cache = None
        assert services.slots(ordering, "takeaway", date(2026, 9, 15), now=now) == []
