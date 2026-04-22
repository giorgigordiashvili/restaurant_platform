"""
SHA-1 HMAC signing for Flitt requests + webhook verification.

Flitt's algorithm (docs: https://docs.flitt.com/api/building-signature/):

    1. Take all body fields whose value is neither empty string nor None.
    2. Sort the *values* lexicographically (string compare).
    3. Prepend the merchant's secret key and join with '|' separators.
    4. SHA-1 the resulting bytes, hex-encoded lowercase.

Both outbound request signing and inbound webhook verification use this
same transform. We keep it as a plain function to make it trivially unit
testable.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any


def _iter_signable_values(payload: dict[str, Any]) -> list[str]:
    """
    Collect non-empty top-level values, coerced to string, for signing.

    Matches Flitt's reference implementation: nested dicts / lists are
    excluded (callers should flatten or sign the outer envelope, not the
    inner object).
    """
    values: list[str] = []
    for key, value in payload.items():
        if key == "signature":
            continue
        if value in (None, ""):
            continue
        if isinstance(value, (dict, list)):
            # Flitt's split `data` field is base64 of a JSON blob; the
            # blob itself is not part of the signature, only the base64
            # string at the top level is.
            continue
        values.append(str(value))
    return values


def sign(payload: dict[str, Any], secret: str) -> str:
    """
    Return the SHA-1 signature for ``payload`` under ``secret``.

    The returned hex string is lowercase, 40 characters long.
    """
    values = sorted(_iter_signable_values(payload), key=str)
    joined = "|".join([secret, *values])
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def verify(payload: dict[str, Any], received_signature: str, secret: str) -> bool:
    """
    Constant-time comparison between a freshly-computed signature and one
    received on a webhook. Returns False on any mismatch or malformed input;
    never raises so the caller can respond with a plain 400.
    """
    if not isinstance(received_signature, str):
        return False
    try:
        expected = sign(payload, secret)
    except Exception:
        return False
    return hmac.compare_digest(expected.lower(), received_signature.lower())
