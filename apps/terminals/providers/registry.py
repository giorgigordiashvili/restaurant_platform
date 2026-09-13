from __future__ import annotations

from .base import TerminalError, TerminalProvider


def get_provider(terminal, *, session=None) -> TerminalProvider:
    code = terminal.provider
    if code == "manual":
        from .manual import ManualProvider

        return ManualProvider(terminal, session=session)
    if code == "bog_link":
        from .bog_link import BogLinkProvider

        return BogLinkProvider(terminal, session=session)
    if code == "tbc_tpay":
        from .tbc_tpay import TbcTpayProvider

        return TbcTpayProvider(terminal, session=session)
    if code == "ecr_bridge":
        from .ecr_bridge import EcrBridgeProvider

        return EcrBridgeProvider(terminal, session=session)
    raise TerminalError(f"Unknown terminal provider '{code}'.")


def is_configured(terminal) -> bool:
    creds = terminal.get_credentials()
    if terminal.provider == "manual":
        return True
    if terminal.provider == "bog_link":
        return bool(creds.get("client_id") and creds.get("client_secret"))
    if terminal.provider == "tbc_tpay":
        return bool(creds.get("apikey") and creds.get("client_id") and creds.get("client_secret"))
    if terminal.provider == "ecr_bridge":
        return bool(terminal.ecr_protocol)
    return False
