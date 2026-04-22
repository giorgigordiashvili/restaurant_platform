"""Tests for apps.payments.splits.compute_split."""

from decimal import Decimal
from unittest.mock import Mock

import pytest

from apps.payments.splits import SplitBreakdown, compute_split


class _FakeRestaurant:
    """Minimal stand-in for the Restaurant model — we only touch one attr."""

    def __init__(self, percent):
        self.platform_commission_percent = percent


@pytest.mark.parametrize(
    "amount,percent,expected_platform,expected_restaurant",
    [
        # Default 5 % — exact.
        (Decimal("100.00"), Decimal("5.00"), Decimal("5.00"), Decimal("95.00")),
        # Fractional order total — platform share rounds half-up to 2dp,
        # restaurant swallows the remainder so sum stays exact.
        (Decimal("125.55"), Decimal("5.00"), Decimal("6.28"), Decimal("119.27")),
        # Per-restaurant override at 3 %.
        (Decimal("125.55"), Decimal("3.00"), Decimal("3.77"), Decimal("121.78")),
        # 0 % override — all to restaurant.
        (Decimal("100.00"), Decimal("0.00"), Decimal("0.00"), Decimal("100.00")),
        # 100 % override — all to platform.
        (Decimal("100.00"), Decimal("100.00"), Decimal("100.00"), Decimal("0.00")),
    ],
)
def test_compute_split_matches_percentages(amount, percent, expected_platform, expected_restaurant):
    restaurant = _FakeRestaurant(percent)
    result = compute_split(amount, restaurant)
    assert isinstance(result, SplitBreakdown)
    assert result.platform_amount == expected_platform
    assert result.restaurant_amount == expected_restaurant
    assert result.platform_amount + result.restaurant_amount == amount


def test_compute_split_falls_back_to_settings_default_when_restaurant_is_none(settings):
    settings.PLATFORM_COMMISSION_PERCENT = "5"
    result = compute_split(Decimal("200.00"), None)
    assert result.platform_amount == Decimal("10.00")
    assert result.restaurant_amount == Decimal("190.00")


def test_compute_split_handles_missing_override_via_default(settings):
    settings.PLATFORM_COMMISSION_PERCENT = "7.5"
    # restaurant exists but has no override — reading returns None-ish.
    restaurant = Mock()
    restaurant.platform_commission_percent = None
    result = compute_split(Decimal("100.00"), restaurant)
    assert result.platform_amount == Decimal("7.50")
    assert result.restaurant_amount == Decimal("92.50")
