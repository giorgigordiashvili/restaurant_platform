"""The restaurant's own riders: nothing to call, the POS moves the status."""

from __future__ import annotations

from decimal import Decimal

from .base import CourierProvider, CreatedResult, QuoteResult


class OwnCourierProvider(CourierProvider):
    code = "own"
    needs_network = False

    def quote(self, delivery) -> QuoteResult:
        return QuoteResult(price=Decimal("0.00"))

    def create(self, delivery) -> CreatedResult:
        return CreatedResult(external_id="", status="requested")

    def cancel(self, delivery, reason: str = "") -> None:
        return None
