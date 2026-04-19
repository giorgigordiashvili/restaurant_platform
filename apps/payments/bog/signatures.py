"""
Webhook signature verification for BOG Payment Manager.

BOG signs the raw request body of callback POSTs with RSA-SHA256 and ships the
base64 signature in the ``Callback-Signature`` header. We verify with the public
key from ``settings.BOG_WEBHOOK_PUBLIC_KEY``.

Docs: https://api.bog.ge/docs/en/payments/standard-process/callback
"""

from __future__ import annotations

import base64
import logging

from django.conf import settings

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

logger = logging.getLogger(__name__)


class SignatureError(Exception):
    """Raised when a webhook signature can't be verified."""


def _load_public_key() -> rsa.RSAPublicKey:
    pem = settings.BOG_WEBHOOK_PUBLIC_KEY
    if not pem:
        raise SignatureError("BOG_WEBHOOK_PUBLIC_KEY is not configured.")

    # Accept either a literal PEM block or a base64-wrapped blob (for env
    # convenience). If the value doesn't contain the PEM header, try decoding
    # base64 first.
    candidate = pem.strip()
    if "-----BEGIN" not in candidate:
        try:
            candidate = base64.b64decode(candidate).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise SignatureError("BOG_WEBHOOK_PUBLIC_KEY is not valid PEM or base64.") from exc

    try:
        key = serialization.load_pem_public_key(candidate.encode("utf-8"))
    except Exception as exc:  # pragma: no cover - cryptography raises several types
        raise SignatureError(f"Failed to load BOG public key: {exc}") from exc

    if not isinstance(key, rsa.RSAPublicKey):
        raise SignatureError("BOG public key is not RSA.")
    return key


def verify_signature(raw_body: bytes, signature_b64: str | None) -> bool:
    """
    Returns True iff ``signature_b64`` (base64) is a valid RSA-SHA256
    signature over ``raw_body`` using BOG's public key. Returns False on any
    verification failure. Raises ``SignatureError`` only on configuration
    problems (missing/invalid public key) — callers should surface those as 503.
    """
    if not signature_b64:
        return False

    public_key = _load_public_key()

    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except ValueError:
        logger.warning("BOG webhook signature is not valid base64.")
        return False

    try:
        public_key.verify(
            signature,
            raw_body,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except InvalidSignature:
        logger.warning("BOG webhook signature did not verify.")
        return False
