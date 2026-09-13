from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class TerminalError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        self.retryable = retryable


@dataclass
class Started:
    status: str = "sent"  # sent | awaiting_confirm
    external_id: str = ""
    pay_url: str = ""
    expires_at: datetime | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Result:
    status: str  # approved | declined | cancelled | timeout | failed | sent (still open)
    external_id: str = ""
    auth_code: str = ""
    card_mask: str = ""
    rrn: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def final(self) -> bool:
        return self.status in ("approved", "declined", "cancelled", "timeout", "failed")


class TerminalProvider:
    code = ""
    polls = False  # the beat task asks ``poll`` for open transactions

    def __init__(self, terminal, *, session=None):
        self.terminal = terminal
        self.session = session

    def start(self, tx) -> Started:  # pragma: no cover - interface
        raise NotImplementedError

    def poll(self, tx) -> Result | None:  # pragma: no cover - interface
        return None

    def cancel(self, tx) -> None:  # pragma: no cover - interface
        return None

    def refund(self, tx, amount) -> Result:  # pragma: no cover - interface
        raise TerminalError("Refunds through this terminal are not supported; refund from the ledger.")
