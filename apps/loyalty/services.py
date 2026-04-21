"""
Platform-wide loyalty helpers.

`accrue_platform_points` is called from payment-completion hooks (BOG
webhook and cash-settle endpoint) and creates a PlatformLoyaltyLedger
row when the order qualifies.

`current_user_tier` sums the user's ledger rows over the last 365 days
and returns the highest-min_points active tier they clear (or None if
the user is below every configured tier).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

from .models import PlatformLoyaltyLedger, PlatformLoyaltyTier

logger = logging.getLogger(__name__)

# Hybrid accrual: one point just for ordering, plus 10% of the order
# total as bonus points. Chosen so that average-spend customers see a
# bit of credit for a tiny order and proportionally more for a big one.
POINTS_PER_ORDER = Decimal("1")
POINTS_PER_GEL = Decimal("0.1")

# Rolling window for tier calculation, in days.
WINDOW_DAYS = 365

# Short per-user cache on the tier calculation so a hot checkout page
# doesn't re-sum the ledger on every request.
_TIER_CACHE_SECONDS = 60


def accrue_platform_points(order, *, source: str) -> None:
    """
    Credit the order's customer with platform-loyalty points.

    No-ops when the restaurant hasn't opted in, when the order was
    placed anonymously (phone-only), or when the customer doesn't have
    a UserProfile row yet (shouldn't happen in prod but paranoid
    guarding costs nothing).
    """
    restaurant = getattr(order, "restaurant", None)
    if not restaurant or not getattr(restaurant, "accepts_platform_loyalty", False):
        return

    customer = getattr(order, "customer", None)
    if not customer:
        return
    profile = getattr(customer, "profile", None)
    if not profile:
        return

    total = order.total or Decimal("0")
    if total <= 0:
        return

    points = (POINTS_PER_ORDER + (total * POINTS_PER_GEL)).quantize(Decimal("0.01"))
    try:
        PlatformLoyaltyLedger.objects.create(
            user=profile,
            order=order,
            restaurant=restaurant,
            points=points,
            source=source,
        )
    except Exception:  # pragma: no cover — defence in depth
        logger.exception("Failed to accrue platform loyalty for order %s", order.id)
        return

    # Invalidate the cached tier so the next status fetch sees the update.
    cache.delete(_tier_cache_key(profile.id))


def _tier_cache_key(profile_id) -> str:
    return f"platform_loyalty:tier:{profile_id}"


def _window_sum(profile_id) -> Decimal:
    since = timezone.now() - timezone.timedelta(days=WINDOW_DAYS)
    agg = PlatformLoyaltyLedger.objects.filter(
        user_id=profile_id,
        earned_at__gte=since,
    ).aggregate(total=Sum("points"))
    return agg["total"] or Decimal("0")


def current_user_tier(user) -> Optional[PlatformLoyaltyTier]:
    """
    Return the tier matching the user's last-365-days points balance,
    or None if the user has no profile / no points / no configured tier
    applies.
    """
    profile = getattr(user, "profile", None)
    if not profile:
        return None

    cached = cache.get(_tier_cache_key(profile.id))
    if cached is not None:
        # cached as the tier's pk (or 0 sentinel meaning "no tier")
        if cached == 0:
            return None
        return PlatformLoyaltyTier.objects.filter(pk=cached, is_active=True).first()

    points = _window_sum(profile.id)
    tier = PlatformLoyaltyTier.objects.filter(is_active=True, min_points__lte=points).order_by("-min_points").first()
    cache.set(_tier_cache_key(profile.id), tier.pk if tier else 0, _TIER_CACHE_SECONDS)
    return tier


def user_status(user) -> dict:
    """
    Aggregate helper used by the public status endpoint. Returns the
    current tier, the next tier (if any), the point total, and the gap
    to next. Safe to call for anonymous users (returns zeros + the
    lowest tier).
    """
    profile = getattr(user, "profile", None)
    points = _window_sum(profile.id) if profile else Decimal("0")

    active = PlatformLoyaltyTier.objects.filter(is_active=True).order_by("min_points")
    current = None
    next_tier = None
    for tier in active:
        if points >= tier.min_points:
            current = tier
        elif next_tier is None:
            next_tier = tier
            break

    gap = (next_tier.min_points - points).quantize(Decimal("0.01")) if next_tier else Decimal("0")
    since = timezone.now() - timezone.timedelta(days=WINDOW_DAYS)
    return {
        "current_tier": current,
        "next_tier": next_tier,
        "points": points.quantize(Decimal("0.01")),
        "points_to_next": gap,
        "window_started": since,
    }
