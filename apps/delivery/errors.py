"""One error type for every platform client so tasks / services retry uniformly."""

from __future__ import annotations

from typing import Any


class PlatformClientError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        self.retryable = retryable
