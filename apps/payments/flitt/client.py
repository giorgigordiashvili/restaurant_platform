"""
Thin HTTP client for Flitt's REST API — no Django imports so it stays
trivially unit-testable.

Three methods map to three endpoints:

* ``create_checkout`` → ``POST /api/checkout/url`` (hosted-page redirect flow)
* ``create_settlement`` → ``POST /api/settlement`` (second-step split fan-out)
* ``reverse_order`` → ``POST /api/reverse/<order_id>`` (refund / reversal)

All requests are signed with SHA-1 HMAC per Flitt's spec. Errors are raised
as :class:`FlittClientError` with the response body attached for debugging.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Any

import requests

from .signatures import sign

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10  # seconds


class FlittClientError(Exception):
    """Raised when Flitt returns a non-successful response."""

    def __init__(self, message: str, *, payload: Any | None = None, status_code: int | None = None):
        super().__init__(message)
        self.payload = payload
        self.status_code = status_code


@dataclass(frozen=True)
class FlittConfig:
    api_url: str
    merchant_id: str
    secret_key: str

    def is_ready(self) -> bool:
        return bool(self.api_url and self.merchant_id and self.secret_key)


class FlittClient:
    """Minimal Flitt REST client."""

    def __init__(self, config: FlittConfig, *, session: requests.Session | None = None):
        self._config = config
        self._session = session or requests.Session()

    # ── checkout ─────────────────────────────────────────────────────────

    def create_checkout(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Submit a checkout request and receive a ``checkout_url`` the browser
        should redirect to.

        ``payload`` must already contain merchant + order + amount keys;
        ``signature`` and ``merchant_id`` are injected here so callers can
        stay provider-agnostic.
        """
        body = {
            **payload,
            "merchant_id": self._config.merchant_id,
        }
        body["signature"] = sign(body, self._config.secret_key)
        return self._post("/api/checkout/url", {"request": body})

    # ── split settlement ─────────────────────────────────────────────────

    def create_settlement(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Two-step split: after a checkout is approved, POST a settlement
        referencing ``operation_id`` (the original order_id) with a
        ``receiver[]`` list.

        Flitt wraps the actual settlement body in a base64-encoded
        ``data`` field inside a signing envelope — unusual but documented
        on https://docs.flitt.com/api/split/.
        """
        encoded = base64.b64encode(json.dumps(data, separators=(",", ":")).encode("utf-8")).decode("ascii")
        envelope = {
            "version": "1.0.1",
            "data": encoded,
            "signature": sign(
                {"data": encoded, "merchant_id": self._config.merchant_id},
                self._config.secret_key,
            ),
        }
        return self._post("/api/settlement", {"request": envelope})

    # ── reversal / refund ────────────────────────────────────────────────

    def reverse_order(self, order_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Issue a reverse (refund) against a previously-approved order.

        ``payload`` should include ``amount`` (integer minor units) and,
        for split-paid orders, a ``receiver[]`` breakdown so Flitt claws
        back proportionally from each beneficiary.
        """
        body = {
            **payload,
            "order_id": order_id,
            "merchant_id": self._config.merchant_id,
        }
        body["signature"] = sign(body, self._config.secret_key)
        return self._post(f"/api/reverse/{order_id}", {"request": body})

    # ── transport ────────────────────────────────────────────────────────

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = self._config.api_url.rstrip("/") + path
        try:
            response = self._session.post(url, json=body, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise FlittClientError(f"Flitt request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError:
            raise FlittClientError(
                "Flitt returned non-JSON response",
                status_code=response.status_code,
                payload=response.text[:500],
            )

        if response.status_code >= 400:
            raise FlittClientError(
                f"Flitt API {path} returned HTTP {response.status_code}",
                payload=data,
                status_code=response.status_code,
            )

        # Flitt wraps successful responses in {"response": {...}} with a
        # "response_status" field. Treat "failure" as an error so callers
        # don't have to branch on a 200+failure combo.
        inner = data.get("response") if isinstance(data, dict) else None
        if isinstance(inner, dict) and inner.get("response_status") == "failure":
            raise FlittClientError(
                inner.get("error_message") or "Flitt reported response_status=failure",
                payload=data,
                status_code=response.status_code,
            )
        return data


_client_cache: FlittClient | None = None


def get_client() -> FlittClient:
    """Lazily build a shared client from Django settings."""
    global _client_cache
    if _client_cache is not None:
        return _client_cache

    from django.conf import settings

    config = FlittConfig(
        api_url=getattr(settings, "FLITT_API_URL", "https://pay.flitt.com"),
        merchant_id=getattr(settings, "FLITT_MERCHANT_ID", ""),
        secret_key=getattr(settings, "FLITT_SECRET_KEY", ""),
    )
    _client_cache = FlittClient(config)
    return _client_cache


def reset_client_cache() -> None:
    """Test helper — drop the cached client so `get_client()` rebuilds."""
    global _client_cache
    _client_cache = None
