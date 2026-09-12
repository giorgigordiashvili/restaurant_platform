"""Per-platform configuration resolved from the link's encrypted credentials or the platform-wide settings."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

GLOVO_STAGE = "https://stageapi.glovoapp.com"
GLOVO_PROD = "https://api.glovoapp.com"
WOLT_PROD = "https://pos-integration-service.wolt.com"
WOLT_DEV = "https://pos-integration-service.development.dev.woltapi.com"
WOLT_AUTH_PROD = "https://integrations-authentication-service.wolt.com/oauth2/token"
WOLT_AUTH_DEV = "https://integrations-authentication-service.development.dev.woltapi.com/oauth2/token"


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
    """OAuth 2.0 (client id / secret + rotating refresh token) or the legacy ``WOLT-API-KEY`` header."""

    venue_id: str
    client_id: str = ""
    client_secret: str = ""
    api_key: str = ""
    base_url: str = WOLT_PROD
    auth_url: str = WOLT_AUTH_PROD

    @property
    def uses_oauth(self) -> bool:
        return bool(self.client_id and self.client_secret)


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


def resolve_wolt_config(link) -> WoltConfig:
    creds = link.get_credentials()
    client_id = creds.get("client_id") or getattr(settings, "WOLT_CLIENT_ID", "")
    client_secret = creds.get("client_secret") or getattr(settings, "WOLT_CLIENT_SECRET", "")
    api_key = creds.get("api_key") or getattr(settings, "WOLT_API_KEY", "")
    if not ((client_id and client_secret) or api_key):
        raise NotConfigured("Wolt credentials missing (client id + secret, or an API key).")
    if not link.store_external_id:
        raise NotConfigured("Wolt venue id missing on the platform link.")
    sandbox = getattr(link, "sandbox", True)
    return WoltConfig(
        venue_id=link.store_external_id,
        client_id=client_id,
        client_secret=client_secret,
        api_key=api_key,
        base_url=(
            getattr(settings, "WOLT_BASE_URL_DEV", WOLT_DEV)
            if sandbox
            else getattr(settings, "WOLT_BASE_URL_PROD", WOLT_PROD)
        ),
        auth_url=(
            getattr(settings, "WOLT_AUTH_URL_DEV", WOLT_AUTH_DEV)
            if sandbox
            else getattr(settings, "WOLT_AUTH_URL_PROD", WOLT_AUTH_PROD)
        ),
    )


def wolt_signature_valid(link, body: bytes, presented: str) -> bool:
    """``WOLT-SIGNATURE`` = hex HMAC-SHA256 of the raw body with the merchant-provided secret (our webhook_token)."""
    import hashlib
    import hmac

    presented = (presented or "").strip().lower()
    if not presented:
        return False
    secrets_ = [t for t in (getattr(link, "webhook_token", ""), getattr(settings, "WOLT_WEBHOOK_SECRET", "")) if t]
    for secret in secrets_:
        digest = hmac.new(secret.encode(), body or b"", hashlib.sha256).hexdigest()
        if hmac.compare_digest(presented, digest):
            return True
    return False
