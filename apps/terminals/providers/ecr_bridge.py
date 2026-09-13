"""ECR terminal through the terminal bridge: we queue the amount, the bridge on the till PC drives the device."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from .base import Result, Started, TerminalProvider


class EcrBridgeProvider(TerminalProvider):
    code = "ecr_bridge"

    def start(self, tx) -> Started:
        # The bridge long-polls ``/api/v1/terminal-bridge/jobs/next/`` and posts the result back.
        return Started(
            status="pending",
            expires_at=timezone.now() + timedelta(seconds=int(self.terminal.timeout_seconds or 180)),
            raw={"protocol": self.terminal.ecr_protocol, "device": self.terminal.connection},
        )

    def cancel(self, tx) -> None:
        return None  # the bridge sees the cancelled status on its next poll

    def refund(self, tx, amount) -> Result:
        # Queued as a refund job for the bridge (services.start_refund creates the row).
        return Result(status="sent")
