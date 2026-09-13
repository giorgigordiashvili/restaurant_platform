"""Glovo On-Demand (Delivery Hero ODR) client + provider. See package docstring for VERIFY ON STAGE."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache

from apps.delivery.config import NotConfigured
from apps.delivery.parsed import parse_dt

from .base import CourierError, CourierProvider, CreatedResult, QuoteResult, StatusResult, recipient_of

logger = logging.getLogger(__name__)

PROD = "https://ondemand-api-glovoapp.deliveryhero.io"
STAGE = "https://api-infra-eu-central-1.stg.ondemandrider.net"
STS = "https://sts.deliveryhero.io/oauth2/token"

STATE_MAP = {
    "NEW": "requested",
    "SCHEDULED": "accepted",
    "ACCEPTED": "accepted",
    "ACTIVE": "assigned",
    "COURIER_ASSIGNED": "assigned",
    "ARRIVED_AT_PICKUP": "assigned",
    "PICKED_UP": "picked_up",
    "IN_DELIVERY": "picked_up",
    "ARRIVED_AT_DELIVERY": "picked_up",
    "DELIVERED": "delivered",
    "CANCELLED": "cancelled",
    "CANCELED": "cancelled",
    "RETURNED": "failed",
    "FAILED": "failed",
}


class GlovoOdrConfig:
    def __init__(
        self, *, client_id, client_secret, vendor_id, callback_secret="", country="ge", base_url=PROD, sts=STS
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.vendor_id = vendor_id
        self.callback_secret = callback_secret
        self.country = country
        self.base_url = base_url
        self.sts = sts

    @property
    def api_root(self) -> str:
        return f"{self.base_url.rstrip('/')}/{self.country}/api/v1"


def resolve_config(link) -> GlovoOdrConfig:
    creds = link.get_credentials() if link is not None else {}
    client_id = creds.get("client_id") or getattr(settings, "GLOVO_ODR_CLIENT_ID", "")
    client_secret = creds.get("client_secret") or getattr(settings, "GLOVO_ODR_CLIENT_SECRET", "")
    vendor_id = (link.store_external_id if link is not None else "") or getattr(settings, "GLOVO_ODR_VENDOR_ID", "")
    if not (client_id and client_secret and vendor_id):
        raise NotConfigured("Glovo On-Demand needs a client id, client secret and vendor id.")
    sandbox = getattr(link, "sandbox", True)
    return GlovoOdrConfig(
        client_id=client_id,
        client_secret=client_secret,
        vendor_id=vendor_id,
        callback_secret=creds.get("callback_secret") or getattr(settings, "GLOVO_ODR_CALLBACK_SECRET", ""),
        country=creds.get("country") or getattr(settings, "GLOVO_ODR_COUNTRY", "ge"),
        base_url=(
            getattr(settings, "GLOVO_ODR_BASE_URL_STAGE", STAGE)
            if sandbox
            else getattr(settings, "GLOVO_ODR_BASE_URL_PROD", PROD)
        ),
        sts=getattr(settings, "GLOVO_ODR_STS_URL", STS),
    )


class GlovoOdrClient:
    def __init__(self, config: GlovoOdrConfig, *, link=None, session=None, timeout: int = 20):
        self.config = config
        self.link = link
        self.timeout = timeout
        self._token = None
        self._token_expires = 0.0
        if session is None:
            import requests

            session = requests.Session()
        self.session = session

    # -- auth ---------------------------------------------------------------

    def _assertion(self) -> str:
        import jwt

        now = int(time.time())
        return jwt.encode(
            {
                "iss": self.config.client_id,
                "sub": self.config.client_id,
                "aud": self.config.sts,
                "iat": now,
                "exp": now + 300,
            },
            self.config.client_secret,
            algorithm="HS256",
        )

    def _token_key(self) -> str:
        return f"glovo-odr:token:{self.link.pk if self.link is not None else self.config.client_id}"

    def _stored_token(self) -> str | None:
        token = getattr(self, "_token", None)
        if token and self._token_expires > time.time():
            return token
        cached = cache.get(self._token_key())
        if cached:
            return cached
        if self.link is not None:
            creds = self.link.get_credentials()
            if creds.get("odr_access_token") and float(creds.get("odr_expires_at") or 0) > time.time():
                return creds["odr_access_token"]
        return None

    def _forget_token(self) -> None:
        self._token = None
        cache.delete(self._token_key())
        if self.link is not None:
            creds = self.link.get_credentials()
            if creds.pop("odr_access_token", None) is not None:
                creds.pop("odr_expires_at", None)
                self.link.set_credentials(creds)
                self.link.save(update_fields=["credentials_encrypted", "updated_at"])

    def access_token(self) -> str:
        token = self._stored_token()
        if token:
            return token
        try:
            r = self.session.request(
                "POST",
                self.config.sts,
                data={
                    "grant_type": "client_credentials",
                    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                    "client_assertion": self._assertion(),
                },
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise CourierError(f"Glovo ODR auth unreachable: {exc}", retryable=True) from exc
        try:
            payload = r.json() if r.text else {}
        except ValueError:
            payload = {"raw": r.text[:500]}
        if r.status_code >= 400 or not payload.get("access_token"):
            raise CourierError(
                f"Glovo ODR auth -> {r.status_code}",
                status_code=r.status_code,
                payload=payload,
                retryable=r.status_code == 429 or r.status_code >= 500,
            )
        ttl = max(int(payload.get("expires_in") or 3600) - 60, 60)
        token = payload["access_token"]
        self._token = token
        self._token_expires = time.time() + ttl
        cache.set(self._token_key(), token, ttl)
        if self.link is not None:
            creds = self.link.get_credentials()
            creds["odr_access_token"] = token
            creds["odr_expires_at"] = self._token_expires
            self.link.set_credentials(creds)
            self.link.save(update_fields=["credentials_encrypted", "updated_at"])
        return token

    # -- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, json_body=None, *, _retry=True) -> dict:
        url = f"{self.config.api_root}{path}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token()}",
        }
        try:
            r = self.session.request(method, url, json=json_body, headers=headers, timeout=self.timeout)
        except CourierError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CourierError(f"Glovo ODR unreachable: {exc}", retryable=True) from exc
        try:
            payload = r.json() if r.text else {}
        except ValueError:
            payload = {"raw": r.text[:1000]}
        if r.status_code == 401 and _retry:
            self._forget_token()
            return self._request(method, path, json_body, _retry=False)
        if r.status_code >= 400:
            raise CourierError(
                f"Glovo ODR {method} {path} -> {r.status_code}",
                status_code=r.status_code,
                payload=payload,
                retryable=r.status_code == 429 or r.status_code >= 500,
            )
        return payload if isinstance(payload, dict) else {"data": payload}

    def create_quote(self, body: dict) -> dict:
        return self._request("POST", "/quotes", body)

    def create_order_from_quote(self, quote_id: str, body: dict | None = None) -> dict:
        return self._request("POST", f"/quotes/{quote_id}", body or {})

    def create_order(self, body: dict) -> dict:
        return self._request("POST", "/orders", body)

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"/orders/{order_id}")

    def cancel_order(self, order_id: str) -> dict:
        return self._request("DELETE", f"/orders/{order_id}")

    def coordinates(self, order_id: str) -> dict:
        return self._request("GET", f"/orders/{order_id}/coordinates")


def build_client(link, *, session=None) -> GlovoOdrClient:
    return GlovoOdrClient(resolve_config(link), link=link, session=session)


def _body(delivery, config: GlovoOdrConfig) -> dict:
    order = delivery.order
    r = order.restaurant
    recipient = recipient_of(order)
    body = {
        "sender": {
            "client_vendor_id": config.vendor_id,
            "name": r.name[:100],
            "phone_number": r.phone or "",
            "location": {
                "address": (r.full_address or r.name)[:200],
                "latitude": float(r.latitude) if r.latitude is not None else None,
                "longitude": float(r.longitude) if r.longitude is not None else None,
            },
        },
        "recipient": {
            "name": recipient["name"],
            "phone_number": recipient["phone"],
            "location": {
                "address": (order.delivery_address or "")[:200],
                "latitude": float(order.delivery_lat),
                "longitude": float(order.delivery_lng),
                "notes": (order.delivery_instructions or "")[:200],
            },
        },
        "client_order_id": order.order_number,
        "amount": float(order.subtotal or 0),
        "currency_code": r.default_currency,
        "payment_method": "PAID",
        "products": [
            {"name": i.item_name[:100], "quantity": int(i.quantity or 1), "price": float(i.unit_price)}
            for i in order.items.exclude(status="cancelled")
        ],
    }
    if order.scheduled_for:
        body["preordered_for"] = order.scheduled_for.isoformat()
    return body


class GlovoOdrProvider(CourierProvider):
    code = "glovo_odr"

    def __init__(self, link=None, *, session=None):
        super().__init__(link, session=session)
        self.client = build_client(link, session=session)

    def quote(self, delivery) -> QuoteResult:
        raw = self.client.create_quote(_body(delivery, self.client.config))
        price = raw.get("quotePrice") or raw.get("price") or {}
        amount = price.get("amount") if isinstance(price, dict) else price
        return QuoteResult(
            price=Decimal(str(amount or 0)).quantize(Decimal("0.01")),
            currency=(price.get("currencyCode") if isinstance(price, dict) else None) or "GEL",
            promise_id=str(raw.get("quoteId") or raw.get("id") or ""),
            dropoff_eta=parse_dt(raw.get("estimatedTimeOfArrival") or raw.get("eta")),
            valid_until=parse_dt(raw.get("expiresAt") or raw.get("validUntil")),
            raw=raw,
        )

    def create(self, delivery) -> CreatedResult:
        quote_id = (delivery.quote or {}).get("promise_id")
        if quote_id:
            raw = self.client.create_order_from_quote(quote_id)
        else:
            raw = self.client.create_order(_body(delivery, self.client.config))
        price = raw.get("quotePrice") or raw.get("price") or {}
        amount = price.get("amount") if isinstance(price, dict) else price
        return CreatedResult(
            external_id=str(raw.get("trackingNumber") or raw.get("orderId") or raw.get("id") or ""),
            status=STATE_MAP.get(str(raw.get("state") or raw.get("status") or "").upper(), "requested"),
            tracking_url=str(raw.get("trackingUrl") or raw.get("tracking_url") or ""),
            cost=Decimal(str(amount)).quantize(Decimal("0.01")) if amount not in (None, "") else None,
            pickup_eta=parse_dt(raw.get("pickupTime") or raw.get("estimatedPickupTime")),
            dropoff_eta=parse_dt(raw.get("estimatedTimeOfArrival") or raw.get("deliveryTime")),
            raw=raw,
        )

    def cancel(self, delivery, reason: str = "") -> None:
        if delivery.external_id:
            self.client.cancel_order(delivery.external_id)

    def refresh(self, delivery) -> StatusResult | None:
        if not delivery.external_id:
            return None
        return status_from_payload(self.client.get_order(delivery.external_id))


def status_from_payload(raw: dict) -> StatusResult:
    courier = raw.get("courier") or raw.get("rider") or {}
    loc = raw.get("courierLocation") or courier.get("location") or {}
    return StatusResult(
        status=STATE_MAP.get(str(raw.get("state") or raw.get("status") or "").upper(), "requested"),
        courier_name=str(courier.get("name") or ""),
        courier_phone=str(courier.get("phoneNumber") or courier.get("phone_number") or ""),
        pickup_eta=parse_dt(raw.get("pickupTime") or raw.get("estimatedPickupTime")),
        dropoff_eta=parse_dt(raw.get("estimatedTimeOfArrival") or raw.get("deliveryTime")),
        tracking_url=str(raw.get("trackingUrl") or raw.get("tracking_url") or ""),
        lat=loc.get("latitude") or loc.get("lat"),
        lng=loc.get("longitude") or loc.get("lng"),
        raw=raw,
    )


def signature_valid(link, body: bytes, presented: str) -> bool:
    try:
        secret = resolve_config(link).callback_secret
    except NotConfigured:
        secret = ""
    if not secret:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    presented = (presented or "").strip().lower().replace("sha256=", "")
    return hmac.compare_digest(expected, presented)
