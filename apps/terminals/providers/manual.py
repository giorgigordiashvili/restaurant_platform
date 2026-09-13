"""The cashier keys the amount into the physical terminal and confirms on the POS."""

from __future__ import annotations

from .base import Result, Started, TerminalProvider


class ManualProvider(TerminalProvider):
    code = "manual"

    def start(self, tx) -> Started:
        return Started(status="awaiting_confirm")

    def cancel(self, tx) -> None:
        return None

    def refund(self, tx, amount) -> Result:
        # The cashier runs the refund on the terminal; we only book it.
        return Result(status="approved")
