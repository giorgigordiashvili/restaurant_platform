"""Wolt Drive (DaaS) client + provider. See package docstring for the VERIFY ON STAGE list."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings

from apps.delivery.config import NotConfigured
from apps.delivery.parsed import minor_to_money, parse_dt

from .base import CourierError, CourierProvider, CreatedResult, QuoteResult, StatusResult, parcels_of, recipient_of

logger = logging.getLogger(__name__)

PROD = "https://daas-public-api.wolt.com"
DEV = "https://daas-public-api.development.dev.woltapi.com"

STATUS_MAP = {
    "INFO_RECEIVED": "requested",
    "RECEIVED": "accepted",
    "ACCEPTED": "accepted",
    "SCHEDULED": "accepted",
    "COURIER_ASSIGNED": "assigned",
    "PICKUP_STARTED": "assigned",
    "PICKED_UP": "picked_up",
    "IN_TRANSIT": "picked_up",
    "DROPOFF_STARTED": "picked_up",
    "DROPOFF_COMPLETED": "delivered",
    "DELIVERED": "delivered",
    "REJECTED": "failed",
    "CANCELLED": "cancelled",
    "CANCELED": "cancelled",
}

EVENT_MAP = {
    "order.received": "accepted",
    "order.rejected": "failed",
    "order.courier_assigned": "assigned",
    "order.pickup_started": "assigned",
    "order.picked_up": "picked_up",
    "order.dropoff_started": "picked_up",
    "order.dropoff_completed": "delivered",
    "order.delivered": "delivered",
    "order.cancelled": "cancelled",
    "order.canceled": "cancelled",
}


class WoltDriveConfig:
    def __init__(self, *, api_key: str, venue_id: str, merchant_id: str = "", client_secret: str = "", base_url=PROD):
        self.api_key = api_key
        self.venue_id = venue_id
        self.merchant_id = merchant_id
        self.client_secret = client_secret
        self.base_url = base_url


def resolve_config(link) -> WoltDriveConfig:
    creds = link.get_credentials() if link is not None else {}
    api_key = creds.get("api_key") or getattr(settings, "WOLT_DRIVE_API_KEY", "")
    venue_id = (link.store_external_id if link is not None else "") or getattr(settings, "WOLT_DRIVE_VENUE_ID", "")
    if not api_key or not venue_id:
        raise NotConfigured("Wolt Drive needs a merchant API key and a venue id.")
    sandbox = getattr(link, "sandbox", True)
    return WoltDriveConfig(
        api_key=api_key,
        venue_id=venue_id,
        merchant_id=creds.get("merchant_id", ""),
        client_secret=creds.get("client_secret") or getattr(settings, "WOLT_DRIVE_CLIENT_SECRET", ""),
        base_url=(
            getattr(settings, "WOLT_DRIVE_BASE_URL_DEV", DEV)
            if sandbox
            else getattr(settings, "WOLT_DRIVE_BASE_URL_PROD", PROD)
        ),
    )


class WoltDriveClient:
    def __init__(self, config: WoltDriveConfig, *, session=None, timeout: int = 20):
        self.config = config
        self.timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        self.session = session

    def _request(self, method: str, path: str, json_body=None) -> dict:
        url = f"{self.config.base_url.rstrip('/')}{path}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        try:
            r = self.session.request(method, url, json=json_body, headers=headers, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            raise CourierError(f"Wolt Drive unreachable: {exc}", retryable=True) from exc
        try:
            payload = r.json() if r.text else {}
        except ValueError:
            payload = {"raw": r.text[:1000]}
        if r.status_code >= 400:
            raise CourierError(
                f"Wolt Drive {method} {path} -> {r.status_code}",
                status_code=r.status_code,
                payload=payload,
                retryable=r.status_code == 429 or r.status_code >= 500,
            )
        return payload if isinstance(payload, dict) else {"data": payload}

    def shipment_promise(self, body: dict) -> dict:
        return self._request("POST", f"/v1/venues/{self.config.venue_id}/shipment-promises", body)

    def create_delivery(self, body: dict) -> dict:
        return self._request("POST", f"/v1/venues/{self.config.venue_id}/deliveries", body)

    def cancel(self, wolt_order_reference_id: str, reason: str = "") -> dict:
        return self._request(
            "PATCH", f"/order/{wolt_order_reference_id}/status/cancel", {"reason": reason or "Restaurant cancelled"}
        )

    def get_delivery(self, delivery_id: str) -> dict:
        return self._request("GET", f"/v1/venues/{self.config.venue_id}/deliveries/{delivery_id}")


def build_client(link, *, session=None) -> WoltDriveClient:
    return WoltDriveClient(resolve_config(link), session=session)


def _dropoff(order) -> dict:
    a = order.address_json or {}
    return {
        "location": {
            "formatted_address": (order.delivery_address or "")[:200],
            "coordinates": {"lat": float(order.delivery_lat), "lon": float(order.delivery_lng)},
        },
        "comment": (order.delivery_instructions or "")[:200],
        "contact_details": {
            "name": recipient_of(order)["name"],
            "phone_number": recipient_of(order)["phone"],
            "send_tracking_link_sms": True,
        },
        "address_details": {k: str(a.get(k, ""))[:50] for k in ("building", "entrance", "floor", "apartment")},
    }


class WoltDriveProvider(CourierProvider):
    code = "wolt_drive"

    def __init__(self, link=None, *, session=None):
        super().__init__(link, session=session)
        self.client = build_client(link, session=session)

    def _promise_body(self, delivery) -> dict:
        order = delivery.order
        body = {
            "dropoff": _dropoff(order),
            "min_preparation_time_minutes": int(
                getattr(delivery, "_lead_minutes", 0) or getattr(order.restaurant, "average_preparation_time", 20)
            ),
        }
        if order.scheduled_for:
            body["scheduled_dropoff_time"] = order.scheduled_for.isoformat()
        return body

    def quote(self, delivery) -> QuoteResult:
        raw = self.client.shipment_promise(self._promise_body(delivery))
        price = raw.get("price") or {}
        return QuoteResult(
            price=minor_to_money(price.get("amount")),
            currency=price.get("currency") or "GEL",
            promise_id=str(raw.get("id") or ""),
            pickup_eta=parse_dt((raw.get("pickup") or {}).get("eta")),
            dropoff_eta=parse_dt((raw.get("dropoff") or {}).get("eta")),
            valid_until=parse_dt(raw.get("valid_until")),
            raw=raw,
        )

    def create(self, delivery) -> CreatedResult:
        order = delivery.order
        promise_id = (delivery.quote or {}).get("promise_id")
        if not promise_id:
            promise_id = self.quote(delivery).promise_id
        body = {
            "shipment_promise_id": promise_id,
            "merchant_order_reference_id": order.order_number,
            "order_number": order.order_number[-5:],
            "dropoff": _dropoff(order),
            "recipient": {
                "name": recipient_of(order)["name"],
                "phone_number": recipient_of(order)["phone"],
                "email": recipient_of(order)["email"] or None,
            },
            "parcels": parcels_of(order),
            "customer_support": {"phone_number": order.restaurant.phone or ""},
            "sms_notifications": {"received": True, "picked_up": True},
            "handshake_delivery": {"is_required": False},
            "contents": [
                {"count": p["count"], "description": p["description"], "identifier": p["identifier"]}
                for p in parcels_of(order)
            ],
            "price": {"amount": int(Decimal(order.subtotal or 0) * 100), "currency": order.restaurant.default_currency},
        }
        if order.scheduled_for:
            body["scheduled_dropoff_time"] = order.scheduled_for.isoformat()
        raw = self.client.create_delivery(body)
        price = raw.get("price") or {}
        return CreatedResult(
            external_id=str(raw.get("wolt_order_reference_id") or raw.get("id") or ""),
            status=STATUS_MAP.get(str(raw.get("status") or "").upper(), "requested"),
            tracking_url=(raw.get("tracking") or {}).get("url", "") or "",
            cost=minor_to_money(price.get("amount")) if price else None,
            pickup_eta=parse_dt((raw.get("pickup") or {}).get("eta")),
            dropoff_eta=parse_dt((raw.get("dropoff") or {}).get("eta")),
            raw=raw,
        )

    def cancel(self, delivery, reason: str = "") -> None:
        if delivery.external_id:
            self.client.cancel(delivery.external_id, reason)

    def refresh(self, delivery) -> StatusResult | None:
        delivery_id = (delivery.quote or {}).get("delivery_id") or ""
        if not delivery_id:
            return None
        raw = self.client.get_delivery(delivery_id)
        return status_from_payload(raw)


def status_from_payload(raw: dict, *, event_type: str = "") -> StatusResult:
    status = EVENT_MAP.get(event_type) or STATUS_MAP.get(str(raw.get("status") or "").upper(), "")
    courier = raw.get("courier") or {}
    loc = courier.get("location") or {}
    return StatusResult(
        status=status or "requested",
        courier_name=str(courier.get("name") or ""),
        courier_phone=str(courier.get("phone_number") or ""),
        pickup_eta=parse_dt((raw.get("pickup") or {}).get("eta")),
        dropoff_eta=parse_dt((raw.get("dropoff") or {}).get("eta")),
        tracking_url=(raw.get("tracking") or {}).get("url", "") or "",
        lat=loc.get("lat"),
        lng=loc.get("lon"),
        raw=raw,
    )


def verify_webhook(link, body: bytes) -> dict:
    """Wolt Drive webhooks are JWTs (HS256 with the merchant client secret). Returns the decoded claims."""
    import json

    import jwt

    cfg = resolve_config(link)
    text = body.decode("utf-8", "replace").strip()
    token = text
    if text.startswith("{"):
        try:
            token = json.loads(text).get("token") or ""
        except ValueError:
            token = ""
    if not token or not cfg.client_secret:
        raise CourierError("Wolt Drive webhook: missing token or client secret.", status_code=401)
    try:
        return jwt.decode(token, cfg.client_secret, algorithms=["HS256"], options={"verify_aud": False})
    except jwt.PyJWTError as exc:
        raise CourierError(f"Wolt Drive webhook: bad signature ({exc}).", status_code=401) from exc
