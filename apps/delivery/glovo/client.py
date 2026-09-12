"""
HTTP client for the Glovo Partners API (BogClient style: injectable session,
typed errors, no token cache -- the integration token is static).

Not exercised against Glovo stage yet -- verified only against recorded
fixtures in tests/delivery/fixtures/glovo.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.delivery.config import GlovoConfig, resolve_glovo_config

logger = logging.getLogger(__name__)


class GlovoClientError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
        self.retryable = retryable


class GlovoClient:
    def __init__(self, config: GlovoConfig, *, session=None, timeout: int = 20):
        self.config = config
        self.timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        self.session = session

    # -- transport ----------------------------------------------------------

    def _headers(self) -> dict:
        token = self.config.api_token
        auth = f"{self.config.auth_scheme} {token}".strip() if self.config.auth_scheme else token
        return {"Authorization": auth, "Accept": "application/json", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, json_body=None) -> dict:
        url = f"{self.config.base_url.rstrip('/')}{path}"
        try:
            r = self.session.request(method, url, json=json_body, headers=self._headers(), timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - transport errors are retryable
            raise GlovoClientError(f"Glovo unreachable: {exc}", retryable=True) from exc
        try:
            payload = r.json() if r.text else {}
        except ValueError:
            payload = {"raw": r.text[:1000]}
        if r.status_code >= 400:
            retryable = r.status_code == 429 or r.status_code >= 500
            raise GlovoClientError(
                f"Glovo {method} {path} -> {r.status_code}",
                status_code=r.status_code,
                payload=payload,
                retryable=retryable,
            )
        return payload if isinstance(payload, dict) else {"data": payload}

    # -- menu ---------------------------------------------------------------

    def upload_menu(self, menu_url: str) -> dict:
        """POST /webhook/stores/{storeId}/menu {"menuUrl": ...} -> 202 {"transactionId"} (docs)."""
        return self._request("POST", f"/webhook/stores/{self.config.store_id}/menu", {"menuUrl": menu_url})

    def menu_upload_status(self, transaction_id: str) -> dict:
        """GET /webhook/stores/{storeId}/menu/{transactionId} -> {"status": PROCESSING|SUCCESS|FAILED, "details"} (docs; VERIFY path)."""
        return self._request("GET", f"/webhook/stores/{self.config.store_id}/menu/{transaction_id}")

    def bulk_update(self, products=(), attributes=()) -> dict:
        """POST /webhook/stores/{storeId}/menu/updates {"products": [...], "attributes": [...]} -> 202 {"transactionId"}."""
        body = {"products": list(products), "attributes": list(attributes)}
        return self._request("POST", f"/webhook/stores/{self.config.store_id}/menu/updates", body)

    # -- orders -------------------------------------------------------------

    def set_order_status(self, order_id: str, status: str) -> dict:
        """PUT /webhook/stores/{storeId}/orders/{orderId}/status {"status": ACCEPTED|READY_FOR_PICKUP|OUT_FOR_DELIVERY}."""
        return self._request(
            "PUT", f"/webhook/stores/{self.config.store_id}/orders/{order_id}/status", {"status": status}
        )

    # -- store --------------------------------------------------------------

    def close_store(self, until_iso: str) -> dict:
        return self._request("PUT", f"/webhook/stores/{self.config.store_id}/closing", {"until": until_iso})

    def open_store(self) -> dict:
        return self._request("DELETE", f"/webhook/stores/{self.config.store_id}/closing")


def build_client(link, *, session=None) -> GlovoClient:
    """Single factory used by tasks / adapter / admin -- tests monkeypatch this."""
    return GlovoClient(resolve_glovo_config(link), session=session)
