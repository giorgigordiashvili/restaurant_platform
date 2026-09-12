"""60 s cache per (restaurant, report, period): live numbers, but a busy dashboard does not re-run the SQL on every refresh."""

from __future__ import annotations

from django.core.cache import cache

TIMEOUT = 60


def cached(restaurant, name: str, period, fn, timeout: int = TIMEOUT):
    key = f"reports:{restaurant.pk}:{name}:{period.start_date}:{period.end_date}"
    return cache.get_or_set(key, fn, timeout)


def invalidate(restaurant) -> None:  # pragma: no cover - convenience for admin actions
    cache.delete_pattern(f"reports:{restaurant.pk}:*") if hasattr(cache, "delete_pattern") else None
