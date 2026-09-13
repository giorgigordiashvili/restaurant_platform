"""TBC Checkout (TPAY) pay-by-link / QR with the terminal's own merchant keys."""

from __future__ import annotations

import time
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .base import Result, Started, TerminalError, TerminalProvider

DEFAULT_BASE = "https://api.tbcbank.ge/v1"
STATUS_MAP = {
    "succeeded": "approved",
    "paymentcompleted": "approved",
    "failed": "declined",
    "expired": "timeout",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "returned": "approved",
    "created": "sent",
    "processing": "sent",
    "waitingconfirm": "sent",
    "cancelpaymentprocessing": "sent",
}


def _base() -> str:
    return getattr(settings, "PUBLIC_API_BASE_URL", "").rstrip("/")


class TbcTpayClient:
    def __init__(
        self,
        *,
        apikey: str,
        client_id: str,
        client_secret: str,
        base_url: str = DEFAULT_BASE,
        session=None,
        timeout=20,
        terminal=None,
    ):
        self.terminal = terminal
        self.apikey = apikey
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._token = ""
        self._token_expires = 0.0
        if session is None:
            import requests

            session = requests.Session()
        self.session = session

    def _call(self, method: str, path: str, *, json_body=None, data=None, auth=False, _retry=True) -> dict:
        headers = {"apikey": self.apikey, "Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.access_token()}"
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            r = self.session.request(
                method, f"{self.base_url}{path}", json=json_body, data=data, headers=headers, timeout=self.timeout
            )
        except TerminalError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TerminalError(f"TBC unreachable: {exc}", retryable=True) from exc
        try:
            payload = r.json() if r.text else {}
        except ValueError:
            payload = {"raw": r.text[:1000]}
        if r.status_code == 401 and auth and _retry:
            self._token = ""
            self._token_expires = 0.0
            self._remember_token()
            return self._call(method, path, json_body=json_body, data=data, auth=auth, _retry=False)
        if r.status_code >= 400:
            raise TerminalError(
                f"TBC {method} {path} -> {r.status_code}",
                status_code=r.status_code,
                payload=payload,
                retryable=r.status_code == 429 or r.status_code >= 500,
            )
        return payload if isinstance(payload, dict) else {"data": payload}

    def _stored_token(self) -> str:
        if self._token and self._token_expires > time.time():
            return self._token
        if self.terminal is not None:
            creds = self.terminal.get_credentials()
            if creds.get("tpay_access_token") and float(creds.get("tpay_expires_at") or 0) > time.time():
                self._token = creds["tpay_access_token"]
                self._token_expires = float(creds["tpay_expires_at"])
                return self._token
        return ""

    def _remember_token(self) -> None:
        if self.terminal is None:
            return
        creds = self.terminal.get_credentials()
        creds["tpay_access_token"] = self._token
        creds["tpay_expires_at"] = self._token_expires
        self.terminal.set_credentials(creds)
        self.terminal.save(update_fields=["credentials_encrypted", "updated_at"])

    def access_token(self) -> str:
        token = self._stored_token()
        if token:
            return token
        payload = self._call(
            "POST",
            "/tpay/access-token",
            data={"client_Id": self.client_id, "client_secret": self.client_secret},
        )
        token = payload.get("access_token")
        if not token:
            raise TerminalError("TBC access token missing", payload=payload)
        self._token = token
        self._token_expires = time.time() + max(int(payload.get("expires_in") or 86000) - 60, 60)
        self._remember_token()
        return token

    def create_payment(self, body: dict) -> dict:
        return self._call("POST", "/tpay/payments", json_body=body, auth=True)

    def get_payment(self, pay_id: str) -> dict:
        return self._call("GET", f"/tpay/payments/{pay_id}", auth=True)

    def cancel_payment(self, pay_id: str, amount) -> dict:
        return self._call("POST", f"/tpay/payments/{pay_id}/cancel", json_body={"amount": float(amount)}, auth=True)


class TbcTpayProvider(TerminalProvider):
    code = "tbc_tpay"
    polls = True

    def __init__(self, terminal, *, session=None):
        super().__init__(terminal, session=session)
        creds = terminal.get_credentials()
        if not (creds.get("apikey") and creds.get("client_id") and creds.get("client_secret")):
            raise TerminalError("TBC apikey / client id / client secret missing on this terminal.")
        self.client = TbcTpayClient(
            apikey=creds["apikey"],
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            base_url=creds.get("base_url") or getattr(settings, "TBC_TPAY_BASE_URL", DEFAULT_BASE),
            session=session,
            terminal=terminal,
        )

    def start(self, tx) -> Started:
        minutes = max(int(self.terminal.timeout_seconds or 180) // 60, 2)
        thank_you = f"{_base()}{reverse('terminals:pay-page', args=[tx.pk])}"
        body = {
            "amount": {"currency": tx.currency, "total": float(tx.total)},
            "returnurl": thank_you,
            "callbackUrl": f"{_base()}{reverse('terminals:tbc-callback')}",
            "extra": str(tx.pk),
            "expirationMinutes": minutes,
            "methods": [5, 7],
            "language": "KA",
            "description": f"{tx.restaurant.name} · {tx.target_label}"[:100],
        }
        response = self.client.create_payment(body)
        pay_id = response.get("payId")
        links = response.get("links") or []
        url = next((l.get("uri") for l in links if str(l.get("rel", "")).lower() == "approval_url"), "")
        if not pay_id or not url:
            raise TerminalError("TBC response missing payId / approval_url", payload=response)
        return Started(
            status="sent",
            external_id=str(pay_id),
            pay_url=url,
            expires_at=timezone.now() + timedelta(minutes=minutes),
            raw={"request": body, "response": response},
        )

    def poll(self, tx) -> Result | None:
        if not tx.external_id:
            return None
        return result_from_payment(self.client.get_payment(tx.external_id))

    def cancel(self, tx) -> None:
        if tx.external_id and tx.status in ("sent", "awaiting_confirm"):
            try:
                self.client.cancel_payment(tx.external_id, tx.total)
            except TerminalError:
                pass

    def refund(self, tx, amount) -> Result:
        self.client.cancel_payment(tx.external_id, amount)
        return Result(status="approved", external_id=tx.external_id)


def result_from_payment(payload: dict) -> Result:
    status = STATUS_MAP.get(str(payload.get("status") or "").lower(), "sent")
    return Result(
        status=status,
        external_id=str(payload.get("payId") or ""),
        auth_code=str(payload.get("approvalCode") or ""),
        card_mask=str(payload.get("cardMask") or payload.get("paymentCardNumber") or ""),
        rrn=str(payload.get("rrn") or payload.get("transactionId") or ""),
        error=str(payload.get("statusDescription") or "") if status in ("declined", "failed") else "",
        raw=payload,
    )
