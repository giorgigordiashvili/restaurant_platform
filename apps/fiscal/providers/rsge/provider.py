"""
RS.ge stub: waybill XML export works today; receipts are not covered by RS
web services (they need a certified cash register / virtual cash register
provider, plugged in later). Network calls refuse until a service user is
configured. UNTESTED LIVE.
"""

from apps.fiscal.providers.base import FiscalProvider, ProviderResult
from apps.fiscal.providers.null import NullProvider
from apps.fiscal.providers.rsge.soap import RsGeWaybillClient
from apps.fiscal.providers.rsge.waybill_xml import build_waybill_xml


class RsGeStubProvider(FiscalProvider):
    code = "rsge_stub"
    is_fiscal = False

    def _client(self):
        creds = self.profile.get_credentials()
        if not creds.get("service_user"):
            return None
        return RsGeWaybillClient(creds["service_user"], creds.get("service_password", ""), session=self.session)

    def issue_receipt(self, document) -> ProviderResult:
        return NullProvider.issue_receipt(self, document)

    issue_refund = issue_receipt

    def send_waybill(self, document) -> ProviderResult:
        client = self._client()
        if client is None:
            return ProviderResult(ok=False, error="RS.ge service user not configured", retryable=False)
        return client.save_waybill(build_waybill_xml(document))

    def health(self) -> ProviderResult:
        client = self._client()
        if client is None:
            return ProviderResult(ok=False, error="RS.ge service user not configured")
        return client.check_user()
