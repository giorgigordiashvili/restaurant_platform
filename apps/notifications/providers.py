"""
Outbound channels. Every provider answers ``ProviderResult``; "not configured"
is a normal outcome (status ``skipped``), never an exception, so a restaurant
without an SMS contract still gets its in-app notifications.

SMS providers (``SMS_PROVIDER``):
* ``none``    -- skipped.
* ``console`` -- logged (dev / tests).
* ``http``    -- any HTTP gateway: ``SMS_HTTP_URL`` (+ optional JSON body template
                 ``SMS_HTTP_BODY`` and headers ``SMS_HTTP_HEADERS``), placeholders
                 ``{to}`` ``{text}`` ``{from}``. Georgian gateways (Magti, Sender.ge,
                 smsoffice.ge) all fit this shape.
* ``twilio``  -- ``TWILIO_ACCOUNT_SID`` / ``TWILIO_AUTH_TOKEN`` / ``TWILIO_FROM``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.parse import quote

from django.conf import settings
from django.core import mail

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


@dataclass
class ProviderResult:
    ok: bool
    skipped: bool = False
    provider: str = ""
    provider_id: str = ""
    error: str = ""
    retryable: bool = False


def new_session():
    """HTTP session for providers (tests swap this)."""
    import requests

    return requests.Session()


# ── email ─────────────────────────────────────────────────────────────────


def email_configured() -> bool:
    backend = getattr(settings, "EMAIL_BACKEND", "")
    if backend.endswith(("locmem.EmailBackend", "console.EmailBackend")):
        return True
    return bool(getattr(settings, "EMAIL_HOST", ""))


def send_email(to: str, subject: str, body: str, *, from_name: str = "") -> ProviderResult:
    if not to or "@" not in to:
        return ProviderResult(ok=False, skipped=True, provider="email", error="no email address")
    if not email_configured():
        return ProviderResult(ok=False, skipped=True, provider="email", error="email not configured")
    sender = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@aimenu.ge")
    if from_name:
        sender = f"{from_name} <{sender}>" if "<" not in sender else sender
    try:
        n = mail.send_mail(subject or "AiMenu", body, sender, [to], fail_silently=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Email to %s failed: %s", to, exc)
        return ProviderResult(ok=False, provider="email", error=str(exc)[:300], retryable=True)
    return ProviderResult(ok=bool(n), provider="email")


# ── sms ───────────────────────────────────────────────────────────────────


def sms_provider() -> str:
    return (getattr(settings, "SMS_PROVIDER", "none") or "none").lower()


def sms_configured() -> bool:
    p = sms_provider()
    if p == "console":
        return True
    if p == "http":
        return bool(getattr(settings, "SMS_HTTP_URL", ""))
    if p == "twilio":
        return bool(getattr(settings, "TWILIO_ACCOUNT_SID", "") and getattr(settings, "TWILIO_AUTH_TOKEN", ""))
    return False


def normalize_phone(raw: str) -> str:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit() or ch == "+")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits and not digits.startswith("+"):
        if len(digits) == 9 and digits[0] == "5":  # Georgian mobile without country code
            digits = "+995" + digits
        elif digits.startswith("995"):
            digits = "+" + digits
    return digits


def send_sms(to: str, text: str, *, from_name: str = "", session=None) -> ProviderResult:
    to = normalize_phone(to)
    if not to:
        return ProviderResult(ok=False, skipped=True, provider="sms", error="no phone number")
    p = sms_provider()
    if not sms_configured():
        return ProviderResult(ok=False, skipped=True, provider=f"sms:{p}", error="sms not configured")
    if p == "console":
        logger.info("SMS to %s: %s", to, text)
        return ProviderResult(ok=True, provider="sms:console", provider_id="console")
    session = session or new_session()
    sender = from_name or getattr(settings, "SMS_FROM", "") or "AiMenu"
    try:
        if p == "twilio":
            sid = settings.TWILIO_ACCOUNT_SID
            r = session.request(
                "POST",
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                data={"To": to, "From": getattr(settings, "TWILIO_FROM", "") or sender, "Body": text},
                auth=(sid, settings.TWILIO_AUTH_TOKEN),
                timeout=15,
            )
            payload = _json(r)
            if r.status_code >= 400:
                return ProviderResult(
                    ok=False,
                    provider="sms:twilio",
                    error=str(payload.get("message") or r.status_code)[:300],
                    retryable=r.status_code == 429 or r.status_code >= 500,
                )
            return ProviderResult(ok=True, provider="sms:twilio", provider_id=str(payload.get("sid", "")))
        # generic HTTP gateway
        url = settings.SMS_HTTP_URL.format(to=quote(to), text=quote(text), **{"from": quote(sender)})
        method = (getattr(settings, "SMS_HTTP_METHOD", "GET") or "GET").upper()
        headers = _json_setting("SMS_HTTP_HEADERS")
        body_template = getattr(settings, "SMS_HTTP_BODY", "")
        json_body = None
        if body_template:
            json_body = json.loads(body_template.replace("{to}", to).replace("{text}", text).replace("{from}", sender))
        r = session.request(method, url, json=json_body, headers=headers or None, timeout=15)
        if r.status_code >= 400:
            return ProviderResult(
                ok=False, provider="sms:http", error=f"gateway {r.status_code}", retryable=r.status_code >= 500
            )
        payload = _json(r)
        pid = str(payload.get("id") or payload.get("message_id") or payload.get("messageId") or "") if payload else ""
        return ProviderResult(ok=True, provider="sms:http", provider_id=pid[:120])
    except Exception as exc:  # noqa: BLE001
        logger.warning("SMS to %s failed: %s", to, exc)
        return ProviderResult(ok=False, provider=f"sms:{p}", error=str(exc)[:300], retryable=True)


# ── expo push ─────────────────────────────────────────────────────────────


def send_expo_push(messages: list[dict], *, session=None) -> list[dict]:
    """POST a batch to Expo; returns one ticket per message ({status: ok|error, details: {error}})."""
    if not messages:
        return []
    session = session or new_session()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    token = getattr(settings, "EXPO_ACCESS_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = session.request("POST", EXPO_PUSH_URL, json=messages, headers=headers, timeout=15)
    payload = _json(r)
    if r.status_code >= 400:
        raise RuntimeError(f"Expo push {r.status_code}: {str(payload)[:200]}")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return [{"status": "error", "details": {"error": "bad response"}}] * len(messages)
    return data


def _json(r) -> dict:
    try:
        payload = r.json() if getattr(r, "text", "") else {}
    except ValueError:
        payload = {}
    return payload if isinstance(payload, dict) else {"data": payload}


def _json_setting(name) -> dict:
    raw = getattr(settings, name, "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}
