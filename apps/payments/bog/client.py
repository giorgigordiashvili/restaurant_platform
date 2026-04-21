"""
Bank of Georgia (BOG) Payment Manager HTTP client.

Thin wrapper around BOG's REST endpoints:

- OAuth2 client-credentials token fetch + in-process TTL cache
- POST /payments/v1/ecommerce/orders        — create an order
- GET  /payments/v1/receipt/{order_id}      — fetch receipt
- POST /payments/v1/payment/refund/{id}     — refund

Kept free of Django model imports so it's trivial to unit test. Raises
``BogClientError`` on any non-2xx response or transport failure.

Docs:
- https://api.bog.ge/docs/en/payments/authentication
- https://api.bog.ge/docs/en/payments/standard-process/create-order
- https://api.bog.ge/docs/en/payments/standard-process/get-payment-details
- https://api.bog.ge/docs/en/payments/refund
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

import requests

logger = logging.getLogger(__name__)

# BOG's docs state `expires_in` is "seconds while the Token is active" but the
# sample shows what looks like a millisecond epoch. We treat the field cautiously:
# cache for at most 45 min, and always refresh on a 401 (see request()).
_TOKEN_MAX_TTL_SECONDS = 45 * 60
# How long before the (reported) expiry we proactively refresh.
_TOKEN_REFRESH_SKEW_SECONDS = 60


class BogClientError(Exception):
    """Any BOG API call that didn't return a usable 2xx response."""

    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass
class _Token:
    access_token: str
    expires_at_monotonic: float


class BogClient:
    """
    Stateful client — safe to keep as a module-level singleton because the only
    mutable state is the cached OAuth token (guarded by a lock).
    """

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        oauth_url: str | None = None,
        api_url: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.client_id = client_id or settings.BOG_CLIENT_ID
        self.client_secret = client_secret or settings.BOG_CLIENT_SECRET
        self.oauth_url = oauth_url or settings.BOG_OAUTH_URL
        self.api_url = (api_url or settings.BOG_API_URL).rstrip("/")
        self._session = session or requests.Session()
        self._token: _Token | None = None
        self._lock = threading.Lock()

    # ── Public API ──────────────────────────────────────────────────────────

    def create_order(self, payload: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        """
        POST /payments/v1/ecommerce/orders.

        Returns BOG's JSON response which includes ``id`` and ``_links.redirect.href``.
        """
        extra_headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._request(
            "POST",
            "/ecommerce/orders",
            json_body=payload,
            extra_headers=extra_headers,
        )

    def get_receipt(self, bog_order_id: str) -> dict[str, Any]:
        """GET /payments/v1/receipt/{order_id}."""
        return self._request("GET", f"/receipt/{bog_order_id}")

    def refund(
        self,
        bog_order_id: str,
        *,
        amount: str | float | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """
        POST /payments/v1/payment/refund/{order_id}.

        Omit ``amount`` for a full refund. BOG's docs specify partial refunds
        only for card/Google Pay/Apple Pay; callers should check the payment
        method before passing a partial amount.
        """
        body: dict[str, Any] = {}
        if amount is not None:
            body["amount"] = amount
        extra_headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._request(
            "POST",
            f"/payment/refund/{bog_order_id}",
            json_body=body,
            extra_headers=extra_headers,
        )

    # ── Token handling ──────────────────────────────────────────────────────

    def _get_token(self, *, force_refresh: bool = False) -> str:
        if not self.client_id or not self.client_secret:
            raise ImproperlyConfigured(
                "BOG_CLIENT_ID / BOG_CLIENT_SECRET are not set. Populate them in the environment."
            )

        with self._lock:
            now = time.monotonic()
            token = self._token
            if token and not force_refresh and token.expires_at_monotonic - _TOKEN_REFRESH_SKEW_SECONDS > now:
                return token.access_token

            logger.debug("Fetching fresh BOG OAuth token")
            response = self._session.post(
                self.oauth_url,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
                timeout=10,
            )
            if response.status_code != 200:
                raise BogClientError(
                    f"BOG OAuth failed with status {response.status_code}",
                    status_code=response.status_code,
                    payload=_safe_json(response),
                )
            body = response.json()
            access = body.get("access_token")
            if not access:
                raise BogClientError("BOG OAuth response missing access_token", payload=body)

            # `expires_in` is unreliable per docs — clamp it.
            try:
                expires_in = int(body.get("expires_in", 0))
            except (TypeError, ValueError):
                expires_in = 0
            if expires_in <= 0 or expires_in > _TOKEN_MAX_TTL_SECONDS * 10:
                # Either zero/unknown or suspiciously huge (millis epoch); clamp.
                ttl = _TOKEN_MAX_TTL_SECONDS
            else:
                ttl = min(expires_in, _TOKEN_MAX_TTL_SECONDS)

            self._token = _Token(access_token=access, expires_at_monotonic=now + ttl)
            return access

    # ── Request helper ──────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        extra_headers: dict[str, str] | None = None,
        _retry_on_401: bool = True,
    ) -> dict[str, Any]:
        url = f"{self.api_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update({k: v for k, v in extra_headers.items() if v is not None})

        try:
            response = self._session.request(
                method,
                url,
                json=json_body,
                headers=headers,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise BogClientError(f"BOG request failed: {exc}") from exc

        if response.status_code == 401 and _retry_on_401:
            logger.info("BOG returned 401, refreshing token and retrying once")
            self._get_token(force_refresh=True)
            return self._request(
                method,
                path,
                json_body=json_body,
                extra_headers=extra_headers,
                _retry_on_401=False,
            )

        if not 200 <= response.status_code < 300:
            raise BogClientError(
                f"BOG {method} {path} returned {response.status_code}",
                status_code=response.status_code,
                payload=_safe_json(response),
            )

        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise BogClientError("BOG response was not JSON", payload=response.text) from exc


# ── Module-level singleton ──────────────────────────────────────────────────

_client_singleton: BogClient | None = None
_client_lock = threading.Lock()


def get_client() -> BogClient:
    global _client_singleton  # noqa: PLW0603
    with _client_lock:
        if _client_singleton is None:
            _client_singleton = BogClient()
    return _client_singleton


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text
