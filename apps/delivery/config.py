"""Per-platform configuration resolved from the link's encrypted credentials or the platform-wide settings."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

GLOVO_STAGE = "https://stageapi.glovoapp.com"
GLOVO_PROD = "https://api.glovoapp.com"


class NotConfigured(Exception):
    pass


@dataclass(frozen=True)
class GlovoConfig:
    api_token: str
    store_id: str
    base_url: str = GLOVO_STAGE
    auth_scheme: str = ""  # "" = raw token in Authorization (per docs); "Bearer" configurable


@dataclass(frozen=True)
class WoltConfig:
    api_key: str
    venue_id: str
    base_url: str = "https://pos-integration-service.wolt.com"


@dataclass(frozen=True)
class BoltFoodConfig:
    client_id: str
    client_secret: str
    provider_id: str
    base_url: str = "https://node.bolt.eu/delivery-provider"


def resolve_glovo_config(link) -> GlovoConfig:
    creds = link.get_credentials()
    token = creds.get("api_token") or getattr(settings, "GLOVO_API_TOKEN", "")
    if not token:
        raise NotConfigured("Glovo API token missing (link credentials or GLOVO_API_TOKEN).")
    if not link.store_external_id:
        raise NotConfigured("Glovo store id missing on the platform link.")
    sandbox = getattr(link, "sandbox", True)
    base = (
        getattr(settings, "GLOVO_BASE_URL_STAGE", GLOVO_STAGE)
        if sandbox
        else getattr(settings, "GLOVO_BASE_URL_PROD", GLOVO_PROD)
    )
    return GlovoConfig(
        api_token=token,
        store_id=link.store_external_id,
        base_url=base,
        auth_scheme=creds.get("auth_scheme") or getattr(settings, "GLOVO_AUTH_SCHEME", ""),
    )


def webhook_token_matches(link, presented: str) -> bool:
    """Per-link webhook token, or the platform-wide GLOVO_WEBHOOK_TOKEN (Glovo issues one per integrator)."""
    from hmac import compare_digest

    presented = (presented or "").strip()
    if presented.lower().startswith("bearer "):
        presented = presented[7:].strip()
    if not presented:
        return False
    expected = [t for t in (getattr(link, "webhook_token", ""), getattr(settings, "GLOVO_WEBHOOK_TOKEN", "")) if t]
    return any(compare_digest(presented, t) for t in expected)
