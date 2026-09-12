"""
Adapter registry: ``get_adapter(link)`` returns the manual adapter unless the
platform row carries API credentials *and* a real adapter is implemented.
"""

from .base import ManualAdapter, PlatformAdapter, PlatformResult

API_ADAPTERS: dict[str, type[PlatformAdapter]] = {}


def get_adapter(link) -> PlatformAdapter:
    """A real adapter only when the delivery module is on and the link is configured; otherwise the checklist."""
    cls = API_ADAPTERS.get(link.platform)
    restaurant = getattr(link, "restaurant", None)
    if (
        cls is not None
        and getattr(restaurant, "delivery_enabled", False)
        and (link.credentials_encrypted or _global_token(link))
    ):
        return cls()
    return ManualAdapter()


def _global_token(link) -> bool:
    from django.conf import settings

    return link.platform == "glovo" and bool(getattr(settings, "GLOVO_API_TOKEN", ""))


__all__ = ["get_adapter", "ManualAdapter", "PlatformAdapter", "PlatformResult", "API_ADAPTERS"]
