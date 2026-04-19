"""
Bank of Georgia (BOG) Payment Manager integration.

Public surface of this package:

- ``BogClient``       — request-layer wrapper around BOG's REST API
- ``BogClientError``  — any non-2xx / network / config failure
- ``verify_signature`` — RSA-SHA256 verifier for the webhook handler
- ``get_client``      — convenience singleton used by views

All BOG-bound settings live in ``settings.BOG_*`` and come from the env.
"""

from .client import BogClient, BogClientError, get_client
from .signatures import SignatureError, verify_signature

__all__ = [
    "BogClient",
    "BogClientError",
    "SignatureError",
    "get_client",
    "verify_signature",
]
