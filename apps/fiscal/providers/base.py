"""Provider contract: whatever issues fiscal documents implements these four calls."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderResult:
    ok: bool
    external_id: str = ""
    fiscal_number: str | None = None
    receipt_url: str = ""
    error: str = ""
    retryable: bool = False
    response: dict = field(default_factory=dict)


class NotConfigured(Exception):
    """The provider needs credentials / settings the restaurant has not entered."""


class FiscalProvider:
    code = ""
    is_fiscal = False  # True only when documents are registered with a tax authority

    def __init__(self, profile, *, session=None):
        self.profile = profile
        self.session = session

    def issue_receipt(self, document) -> ProviderResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def issue_refund(self, document) -> ProviderResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def send_waybill(self, document) -> ProviderResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def health(self) -> ProviderResult:
        return ProviderResult(ok=True)
