"""
HTTP client for the Wolt POS integration service (Order, Menu and Venue
APIs). Same shape as GlovoClient: injectable session, typed errors, one
factory the tests monkeypatch.
"""

from __future__ import annotations

import logging

from apps.delivery.config import WoltConfig, resolve_wolt_config
from apps.delivery.errors import PlatformClientError
from apps.delivery.wolt import auth

logger = logging.getLogger(__name__)


class WoltClientError(PlatformClientError):
    pass


class WoltClient:
    def __init__(self, config: WoltConfig, *, link=None, session=None, timeout: int = 20):
        self.config = config
        self.link = link
        self.timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        self.session = session

    # -- transport ----------------------------------------------------------

    def _headers(self) -> dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.config.uses_oauth:
            headers["Authorization"] = f"Bearer {auth.access_token(self.link, self.config, self.session)}"
        else:
            headers["WOLT-API-KEY"] = self.config.api_key
        return headers

    def _request(self, method: str, path: str, json_body=None, *, _retry=True):
        url = f"{self.config.base_url.rstrip('/')}{path}"
        try:
            r = self.session.request(method, url, json=json_body, headers=self._headers(), timeout=self.timeout)
        except PlatformClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport errors are retryable
            raise WoltClientError(f"Wolt unreachable: {exc}", retryable=True) from exc
        try:
            payload = r.json() if r.text else {}
        except ValueError:
            payload = {"raw": r.text[:1000]}
        if r.status_code == 401 and self.config.uses_oauth and _retry and self.link is not None:
            auth.forget_token(self.link)
            return self._request(method, path, json_body, _retry=False)
        if r.status_code >= 400:
            raise WoltClientError(
                f"Wolt {method} {path} -> {r.status_code}",
                status_code=r.status_code,
                payload=payload,
                retryable=r.status_code == 429 or r.status_code >= 500,
            )
        return payload if isinstance(payload, dict) else {"data": payload}

    # -- orders -------------------------------------------------------------

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"/orders/{order_id}")

    def get_order_v2(self, order_id: str) -> dict:
        """Detailed price breakdown (discounts, fees, VAT parts)."""
        return self._request("GET", f"/v2/orders/{order_id}")

    def accept(self, order_id: str, *, adjusted_pickup_time: str | None = None) -> dict:
        body = {"adjusted_pickup_time": adjusted_pickup_time} if adjusted_pickup_time else {}
        return self._request("PUT", f"/orders/{order_id}/accept", body)

    def self_delivery_accept(self, order_id: str, *, adjusted_pickup_time=None, delivery_time=None) -> dict:
        body = {}
        if adjusted_pickup_time:
            body["adjusted_pickup_time"] = adjusted_pickup_time
        if delivery_time:
            body["delivery_time"] = delivery_time
        return self._request("PUT", f"/orders/{order_id}/self-delivery/accept", body)

    def reject(self, order_id: str, reason: str) -> dict:
        return self._request("PUT", f"/orders/{order_id}/reject", {"reason": (reason or "Restaurant rejected")[:500]})

    def ready(self, order_id: str) -> dict:
        return self._request("PUT", f"/orders/{order_id}/ready")

    def delivered(self, order_id: str) -> dict:
        return self._request("PUT", f"/orders/{order_id}/delivered")

    def pickup_completed(self, order_id: str) -> dict:
        return self._request("PUT", f"/orders/{order_id}/pickup-completed")

    def courier_at_customer(self, order_id: str) -> dict:
        return self._request("PUT", f"/orders/{order_id}/courier-at-customer")

    def confirm_preorder(self, order_id: str) -> dict:
        return self._request("PUT", f"/orders/{order_id}/confirm-preorder")

    def refund_items(self, order_id: str, items: list[dict]) -> dict:
        """POST /orders/{id}/refund-items {"items": [{"id": <wolt item id>, "count": n}]}."""
        return self._request("POST", f"/orders/{order_id}/refund-items", {"items": list(items)})

    # -- menu ---------------------------------------------------------------

    def push_menu(self, menu: dict) -> dict:
        return self._request("POST", f"/v1/restaurants/{self.config.venue_id}/menu", menu)

    def get_menu(self) -> dict:
        return self._request("GET", f"/v2/venues/{self.config.venue_id}/menu")

    def update_items(self, data: list[dict]) -> dict:
        """PATCH /venues/{venueId}/items {"data": [{"external_id", "enabled", "in_stock", "price"}]}."""
        return self._request("PATCH", f"/venues/{self.config.venue_id}/items", {"data": list(data)})

    def update_inventory(self, data: list[dict]) -> dict:
        """PATCH /venues/{venueId}/items/inventory {"data": [{"external_id" | "sku", "inventory": n}]}."""
        return self._request("PATCH", f"/venues/{self.config.venue_id}/items/inventory", {"data": list(data)})

    def update_option_values(self, data: list[dict]) -> dict:
        return self._request("PATCH", f"/venues/{self.config.venue_id}/options/values", {"data": list(data)})

    # -- venue --------------------------------------------------------------

    def venue_status(self) -> dict:
        return self._request("GET", f"/venues/{self.config.venue_id}/status")

    def set_online(self, online: bool, *, until_iso: str | None = None) -> dict:
        body = {"status": "ONLINE" if online else "OFFLINE"}
        if not online and until_iso:
            body["until"] = until_iso
        return self._request("PATCH", f"/venues/{self.config.venue_id}/online", body)

    def set_opening_times(self, availabilities: list[dict]) -> dict:
        return self._request(
            "PATCH", f"/venues/{self.config.venue_id}/opening-times", {"availabilities": list(availabilities)}
        )


def build_client(link, *, session=None) -> WoltClient:
    """Single factory used by tasks / adapter / admin -- tests monkeypatch this."""
    return WoltClient(resolve_wolt_config(link), link=link, session=session)
