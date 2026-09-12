"""
Minimal SOAP client for RS.ge WayBillService (``services.rs.ge``).

Written against the public documentation only; every call is marked
UNTESTED LIVE. The HTTP session is injectable so tests replay canned
responses (the same pattern as apps.payments.bog.client).
"""

from __future__ import annotations

import re

from apps.fiscal.providers.base import ProviderResult

RSGE_WAYBILL_URL = "https://services.rs.ge/WayBillService/WayBillService.asmx"
NS = "http://tempuri.org/"


def envelope(method: str, **params) -> bytes:
    body = "".join(f"<{k}>{_escape(v)}</{k}>" for k, v in params.items())
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<soap:Body><{method} xmlns="{NS}">{body}</{method}></soap:Body></soap:Envelope>'
    ).encode("utf-8")


def _escape(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class RsGeWaybillClient:
    def __init__(
        self, service_user: str, service_password: str, *, url: str | None = None, session=None, timeout: int = 20
    ):
        self.su = service_user
        self.sp = service_password
        self.url = url or RSGE_WAYBILL_URL
        self.timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        self.session = session

    def _call(self, method: str, **params) -> tuple[int, str]:
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f"{NS}{method}"}
        r = self.session.post(
            self.url, data=envelope(method, su=self.su, sp=self.sp, **params), headers=headers, timeout=self.timeout
        )
        return r.status_code, r.text

    @staticmethod
    def _result(text: str, method: str) -> str | None:
        m = re.search(rf"<{method}Result>(.*?)</{method}Result>", text, re.S)
        return m.group(1).strip() if m else None

    def check_user(self) -> ProviderResult:
        """``chek_service_user`` (sic — the RS method name). UNTESTED LIVE."""
        try:
            status, text = self._call("chek_service_user")
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(ok=False, error=str(exc), retryable=True)
        result = self._result(text, "chek_service_user")
        ok = status == 200 and (result or "").lower() == "true"
        return ProviderResult(
            ok=ok, error="" if ok else f"RS.ge rejected the service user ({status})", response={"raw": text[:2000]}
        )

    def save_waybill(self, waybill_xml: bytes) -> ProviderResult:
        """``save_waybill``: RESULT > 0 is the waybill id, negative codes are errors. UNTESTED LIVE."""
        try:
            status, text = self._call("save_waybill", waybill=waybill_xml)
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(ok=False, error=str(exc), retryable=True)
        if status != 200:
            return ProviderResult(
                ok=False, error=f"HTTP {status}", retryable=status >= 500, response={"raw": text[:2000]}
            )
        m = re.search(r"<(?:\w+:)?RESULT>\s*(-?\d+)\s*</(?:\w+:)?RESULT>", text)
        if not m:
            return ProviderResult(
                ok=False, error="Unreadable RS.ge response", retryable=False, response={"raw": text[:2000]}
            )
        code = int(m.group(1))
        if code > 0:
            return ProviderResult(ok=True, external_id=str(code), response={"result": code})
        return ProviderResult(ok=False, error=f"RS.ge error code {code}", retryable=False, response={"result": code})
