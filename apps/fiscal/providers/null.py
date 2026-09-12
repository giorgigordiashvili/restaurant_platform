"""Records documents locally with our own sequential numbers. Nothing leaves the server."""

from apps.fiscal.providers.base import FiscalProvider, ProviderResult


class NullProvider(FiscalProvider):
    code = "none"
    is_fiscal = False

    def issue_receipt(self, document) -> ProviderResult:
        return ProviderResult(ok=True, external_id=f"internal:{document.fiscal_number}")

    issue_refund = issue_receipt

    def send_waybill(self, document) -> ProviderResult:
        return ProviderResult(ok=True, external_id="")
