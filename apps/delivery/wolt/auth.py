"""
Bearer tokens for the Wolt API. Access tokens (1 h) are cached; refresh
tokens are single-use, so the rotated one is written back to the link's
encrypted credentials under a short cache lock (two workers refreshing at
once would burn the token).
"""

from __future__ import annotations

import base64
import logging
import time

from django.core.cache import cache

from apps.delivery.errors import PlatformClientError

logger = logging.getLogger(__name__)

SKEW = 60


class WoltAuthError(PlatformClientError):
    pass


def _token_key(link) -> str:
    return f"wolt:token:{link.pk}"


def _lock_key(link) -> str:
    return f"wolt:token-lock:{link.pk}"


def _basic(config) -> str:
    return "Basic " + base64.b64encode(f"{config.client_id}:{config.client_secret}".encode()).decode()


def forget_token(link, *, persist: bool = True) -> None:
    cache.delete(_token_key(link))
    if persist:
        creds = link.get_credentials()
        if creds.pop("access_token", None) is not None or creds.pop("access_expires_at", None) is not None:
            link.set_credentials(creds)
            link.save(update_fields=["credentials_encrypted", "updated_at"])


def _stored(link):
    """Access token kept in the link's encrypted credentials too (survives cache flushes / dummy caches)."""
    cached = cache.get(_token_key(link))
    if cached and cached.get("expires_at", 0) - SKEW > time.time():
        return cached["access_token"]
    creds = link.get_credentials()
    if creds.get("access_token") and float(creds.get("access_expires_at") or 0) - SKEW > time.time():
        return creds["access_token"]
    return None


def access_token(link, config, session, *, timeout=15) -> str:
    """Cached access token, refreshed (and rotated) when missing or about to expire."""
    token = _stored(link)
    if token:
        return token
    for _ in range(20):
        if cache.add(_lock_key(link), 1, 20):
            break
        time.sleep(0.25)
        link.refresh_from_db(fields=["credentials_encrypted"])
        token = _stored(link)
        if token:
            return token
    try:
        return _refresh(link, config, session, timeout)
    finally:
        cache.delete(_lock_key(link))


def _refresh(link, config, session, timeout) -> str:
    creds = link.get_credentials()
    refresh = creds.get("refresh_token") or ""
    code = creds.get("authorization_code") or ""
    if refresh:
        form = {"grant_type": "refresh_token", "refresh_token": refresh}
    elif code:
        form = {"grant_type": "authorization_code", "code": code}
        from django.conf import settings

        redirect = getattr(settings, "WOLT_OAUTH_REDIRECT_URI", "")
        if redirect:
            form["redirect_uri"] = redirect
    else:
        raise WoltAuthError("Wolt OAuth: no refresh token or authorization code on the link.")
    try:
        r = session.request(
            "POST",
            config.auth_url,
            data=form,
            headers={"Authorization": _basic(config), "Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        raise WoltAuthError(f"Wolt auth unreachable: {exc}", retryable=True) from exc
    try:
        payload = r.json() if r.text else {}
    except ValueError:
        payload = {"raw": r.text[:500]}
    if r.status_code >= 400 or not payload.get("access_token"):
        raise WoltAuthError(
            f"Wolt auth -> {r.status_code}",
            status_code=r.status_code,
            payload=payload,
            retryable=r.status_code == 429 or r.status_code >= 500,
        )
    token = payload["access_token"]
    ttl = int(payload.get("expires_in") or 3600)
    expires_at = time.time() + ttl
    cache.set(_token_key(link), {"access_token": token, "expires_at": expires_at}, ttl)
    creds = link.get_credentials()
    creds["access_token"] = token
    creds["access_expires_at"] = expires_at
    if payload.get("refresh_token"):
        creds["refresh_token"] = payload["refresh_token"]
    creds.pop("authorization_code", None)
    link.set_credentials(creds)
    link.save(update_fields=["credentials_encrypted", "updated_at"])
    return token
