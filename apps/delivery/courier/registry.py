from __future__ import annotations

from apps.delivery.config import NotConfigured

from .base import CourierProvider


def link_for(restaurant, provider: str):
    if provider == "own":
        return None
    from apps.delivery.models import RestaurantDeliveryPlatform

    return RestaurantDeliveryPlatform.objects.filter(restaurant=restaurant, platform=provider).first()


def is_configured(restaurant, provider: str) -> bool:
    if provider == "own":
        return True
    link = link_for(restaurant, provider)
    if link is None or not link.is_enabled:
        return False
    try:
        if provider == "wolt_drive":
            from .wolt_drive import resolve_config
        elif provider == "glovo_odr":
            from .glovo_odr import resolve_config
        else:
            return False
        resolve_config(link)
        return True
    except NotConfigured:
        return False


def get_provider(restaurant, provider: str, *, session=None) -> CourierProvider:
    if provider == "own":
        from .own import OwnCourierProvider

        return OwnCourierProvider()
    link = link_for(restaurant, provider)
    if link is None or not link.is_enabled:
        raise NotConfigured(f"{provider} is not connected for this restaurant.")
    if provider == "wolt_drive":
        from .wolt_drive import WoltDriveProvider

        return WoltDriveProvider(link, session=session)
    if provider == "glovo_odr":
        from .glovo_odr import GlovoOdrProvider

        return GlovoOdrProvider(link, session=session)
    raise NotConfigured(f"Unknown courier provider '{provider}'.")
