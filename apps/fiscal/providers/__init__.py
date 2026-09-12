"""Provider registry. ``settings.FISCAL_PROVIDERS`` may add ``{"code": "dotted.path.Class"}`` later."""

from __future__ import annotations

from django.conf import settings
from django.utils.module_loading import import_string

from apps.fiscal.providers.base import FiscalProvider, NotConfigured, ProviderResult
from apps.fiscal.providers.null import NullProvider
from apps.fiscal.providers.rsge.provider import RsGeStubProvider

PROVIDERS: dict[str, type[FiscalProvider]] = {"none": NullProvider, "rsge_stub": RsGeStubProvider}


def provider_class(code: str) -> type[FiscalProvider]:
    extra = getattr(settings, "FISCAL_PROVIDERS", {}) or {}
    if code in extra:
        return import_string(extra[code])
    return PROVIDERS.get(code, NullProvider)


def get_provider(profile, *, session=None) -> FiscalProvider:
    code = getattr(profile, "provider", None) or getattr(settings, "FISCAL_DEFAULT_PROVIDER", "none")
    return provider_class(code)(profile, session=session)


__all__ = [
    "FiscalProvider",
    "NotConfigured",
    "ProviderResult",
    "NullProvider",
    "RsGeStubProvider",
    "get_provider",
    "PROVIDERS",
]
