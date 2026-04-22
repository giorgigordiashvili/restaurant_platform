"""
Platform-commission split arithmetic shared by the BOG + Flitt flows.

Both providers need to cut a paid order in two: the platform's commission
(hard-coded default 5 %, overridable per-restaurant via
``Restaurant.platform_commission_percent``) and the restaurant's share.
Keeping this in one module means the two providers compute identical
splits on identical inputs — important for auditability when the refund
flow has to claw back proportionally.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from apps.tenants.models import Restaurant


TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class SplitBreakdown:
    """Per-order split, expressed in the same currency as ``total``."""

    total: Decimal
    platform_amount: Decimal
    restaurant_amount: Decimal
    platform_percent: Decimal
    restaurant_percent: Decimal

    def to_dict(self) -> dict[str, str]:
        return {
            "total": str(self.total),
            "platform_amount": str(self.platform_amount),
            "restaurant_amount": str(self.restaurant_amount),
            "platform_percent": str(self.platform_percent),
            "restaurant_percent": str(self.restaurant_percent),
        }


def _default_percent() -> Decimal:
    """Read the platform-wide default from settings as a Decimal."""
    raw = getattr(settings, "PLATFORM_COMMISSION_PERCENT", "5")
    # settings may be a Decimal already (cast=Decimal) or a str / int depending
    # on how python-decouple parses the env var.
    return raw if isinstance(raw, Decimal) else Decimal(str(raw))


def compute_split(amount: Decimal, restaurant: "Restaurant | None" = None) -> SplitBreakdown:
    """
    Carve ``amount`` into (platform_amount, restaurant_amount) using the
    restaurant's per-restaurant override when present, else the platform
    default. Platform share is rounded half-up to two decimal places; the
    restaurant share takes whatever remainder keeps the sum exact, so we
    never overcharge the customer by a subunit due to rounding.
    """
    amount = Decimal(amount)
    percent: Decimal
    if restaurant is not None and restaurant.platform_commission_percent is not None:
        percent = restaurant.platform_commission_percent
    else:
        percent = _default_percent()

    platform = (amount * percent / Decimal(100)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    # Clamp in case of pathological inputs — platform share can't exceed
    # the order total.
    if platform > amount:
        platform = amount
    restaurant_amount = (amount - platform).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    return SplitBreakdown(
        total=amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP),
        platform_amount=platform,
        restaurant_amount=restaurant_amount,
        platform_percent=percent,
        restaurant_percent=Decimal(100) - percent,
    )
