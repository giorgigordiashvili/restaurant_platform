"""BOG pay-by-link / QR: a Payment Manager order created with the terminal's own merchant keys."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.payments.bog.client import BogClient, BogClientError

from .base import Result, Started, TerminalError, TerminalProvider

STATUS_MAP = {
    "completed": "approved",
    "rejected": "declined",
    "refunded": "approved",
    "partial_refunded": "approved",
    "created": "sent",
    "processing": "sent",
    "blocked": "sent",
    "refund_requested": "approved",
}


def _base() -> str:
    return getattr(settings, "PUBLIC_API_BASE_URL", "").rstrip("/")


_clients: dict[str, BogClient] = {}


def _client_for(terminal, creds: dict, session) -> BogClient:
    """One BogClient per terminal (it caches the OAuth token); an injected session (tests) bypasses the cache."""
    if session is not None:
        return BogClient(
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            oauth_url=creds.get("oauth_url") or None,
            api_url=creds.get("api_url") or None,
            session=session,
        )
    key = f"{terminal.pk}:{creds['client_id']}:{hash(creds['client_secret'])}"
    client = _clients.get(key)
    if client is None:
        client = _clients[key] = BogClient(
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            oauth_url=creds.get("oauth_url") or None,
            api_url=creds.get("api_url") or None,
        )
    return client


class BogLinkProvider(TerminalProvider):
    code = "bog_link"
    polls = True

    def __init__(self, terminal, *, session=None):
        super().__init__(terminal, session=session)
        creds = terminal.get_credentials()
        if not (creds.get("client_id") and creds.get("client_secret")):
            raise TerminalError("BOG client id / secret missing on this terminal.")
        self.client = _client_for(terminal, creds, session)

    def start(self, tx) -> Started:
        minutes = max(int(self.terminal.timeout_seconds or 180) // 60, 2)
        thank_you = f"{_base()}{reverse('terminals:pay-page', args=[tx.pk])}"
        payload = {
            "callback_url": f"{_base()}{reverse('terminals:bog-callback')}",
            "external_order_id": str(tx.pk),
            "payment_method": ["card", "google_pay", "apple_pay"],
            "ttl": minutes,
            "purchase_units": {
                "currency": tx.currency,
                "total_amount": float(tx.total),
                "basket": [
                    {
                        "product_id": tx.order.order_number if tx.order_id else "bill",
                        "description": f"{tx.restaurant.name} · {tx.target_label}"[:100],
                        "quantity": 1,
                        "unit_price": float(tx.total),
                        "total_price": float(tx.total),
                    }
                ],
            },
            "redirect_urls": {"success": f"{thank_you}?status=ok", "fail": f"{thank_you}?status=fail"},
        }
        try:
            response = self.client.create_order(payload, idempotency_key=str(tx.pk))
        except BogClientError as exc:
            raise TerminalError(str(exc), status_code=exc.status_code, payload=exc.payload, retryable=False) from exc
        order_id = response.get("id")
        url = (response.get("_links") or {}).get("redirect", {}).get("href")
        if not order_id or not url:
            raise TerminalError("BOG response missing id / redirect.href", payload=response)
        return Started(
            status="sent",
            external_id=str(order_id),
            pay_url=url,
            expires_at=timezone.now() + timedelta(minutes=minutes),
            raw={"request": payload, "response": response},
        )

    def poll(self, tx) -> Result | None:
        if not tx.external_id:
            return None
        try:
            receipt = self.client.get_receipt(tx.external_id)
        except BogClientError as exc:
            raise TerminalError(str(exc), status_code=exc.status_code, payload=exc.payload, retryable=True) from exc
        return result_from_receipt(receipt)

    def cancel(self, tx) -> None:
        return None  # BOG links simply expire (ttl); nothing to call

    def refund(self, tx, amount) -> Result:
        try:
            self.client.refund(tx.external_id, amount=str(amount), idempotency_key=f"rf-{tx.pk}-{amount}")
        except BogClientError as exc:
            raise TerminalError(str(exc), status_code=exc.status_code, payload=exc.payload) from exc
        return Result(status="approved", external_id=tx.external_id)


def result_from_receipt(receipt: dict) -> Result:
    key = str((receipt.get("order_status") or {}).get("key") or "").lower()
    status = STATUS_MAP.get(key, "sent")
    detail = receipt.get("payment_detail") or {}
    return Result(
        status=status,
        external_id=str(receipt.get("order_id") or receipt.get("id") or ""),
        auth_code=str(detail.get("auth_code") or ""),
        card_mask=str(detail.get("card_type") or "") + (" " + detail.get("pan", "")[-4:] if detail.get("pan") else ""),
        rrn=str(detail.get("transaction_id") or ""),
        error=str((receipt.get("reject_reason") or "")) if status == "declined" else "",
        raw=receipt,
    )
