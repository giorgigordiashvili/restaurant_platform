"""
Adapter registry: ``get_adapter(link)`` returns the manual adapter unless the
platform row carries API credentials *and* a real adapter is implemented.
"""

from .base import ManualAdapter, PlatformAdapter, PlatformResult

API_ADAPTERS: dict[str, type[PlatformAdapter]] = {}


def get_adapter(link) -> PlatformAdapter:
    cls = API_ADAPTERS.get(link.platform)
    if cls is not None and link.credentials_encrypted:
        return cls()
    return ManualAdapter()


__all__ = ["get_adapter", "ManualAdapter", "PlatformAdapter", "PlatformResult", "API_ADAPTERS"]
