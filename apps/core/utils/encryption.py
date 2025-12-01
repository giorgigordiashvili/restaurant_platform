"""
Field-level encryption utilities for sensitive data.
"""

import base64
import logging

from django.conf import settings

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


def get_fernet_key():
    """
    Get or generate a Fernet encryption key.
    """
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
    if not key:
        raise ValueError("FIELD_ENCRYPTION_KEY must be set in settings")

    # Ensure key is properly formatted
    if len(key) == 32:
        # Convert 32-byte string to base64 URL-safe key
        key = base64.urlsafe_b64encode(key.encode()[:32])
    elif len(key) == 44:
        # Already base64 encoded
        key = key.encode()
    else:
        # Try to use as-is
        key = base64.urlsafe_b64encode(key.encode()[:32])

    return Fernet(key)


def encrypt_field(value):
    """
    Encrypt a string value.

    Args:
        value: The string value to encrypt

    Returns:
        The encrypted value as a string, or None if input is None
    """
    if value is None:
        return None

    try:
        f = get_fernet_key()
        encrypted = f.encrypt(value.encode())
        return encrypted.decode()
    except Exception as e:
        logger.error(f"Encryption error: {e}")
        raise ValueError("Failed to encrypt value")


def decrypt_field(value):
    """
    Decrypt an encrypted string value.

    Args:
        value: The encrypted string value

    Returns:
        The decrypted value as a string, or None if input is None
    """
    if value is None:
        return None

    try:
        f = get_fernet_key()
        decrypted = f.decrypt(value.encode())
        return decrypted.decode()
    except InvalidToken:
        logger.error("Invalid token - data may be corrupted or key changed")
        return value  # Return as-is if decryption fails
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        return value


class EncryptedMixin:
    """
    Mixin for Django model fields that encrypts data at rest.

    Usage:
        class MyModel(models.Model):
            sensitive_data = EncryptedCharField(max_length=500)
    """

    def get_prep_value(self, value):
        """Encrypt value before saving to database."""
        value = super().get_prep_value(value)
        return encrypt_field(value)

    def from_db_value(self, value, expression, connection):
        """Decrypt value when reading from database."""
        if value is None:
            return value
        return decrypt_field(value)

    def to_python(self, value):
        """Convert value to Python object."""
        if value is None:
            return value
        # Don't decrypt here - let from_db_value handle it
        return value
