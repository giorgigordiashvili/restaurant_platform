"""Tests for apps.payments.flitt.signatures — the SHA-1 HMAC envelope."""

import hashlib

from apps.payments.flitt.signatures import sign, verify

SECRET = "test_secret_key"


def _raw_sha1(parts: list[str]) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def test_sign_is_sha1_of_secret_prepended_sorted_values():
    payload = {"merchant_id": 42, "order_id": "ord-1", "amount": 1000, "currency": "GEL"}
    expected = _raw_sha1([SECRET, "1000", "42", "GEL", "ord-1"])
    assert sign(payload, SECRET) == expected


def test_sign_drops_empty_and_nested_values():
    payload = {
        "merchant_id": 42,
        "order_id": "ord-1",
        "notes": "",
        "ignored_dict": {"foo": "bar"},
        "ignored_list": [1, 2],
        "currency": None,
    }
    # Only merchant_id + order_id survive.
    expected = _raw_sha1([SECRET, "42", "ord-1"])
    assert sign(payload, SECRET) == expected


def test_sign_excludes_existing_signature_field_on_reentry():
    """Re-signing a payload that already carries a `signature` key must ignore it."""
    payload = {"merchant_id": 42, "signature": "not_a_real_sig"}
    expected = _raw_sha1([SECRET, "42"])
    assert sign(payload, SECRET) == expected


def test_verify_is_case_insensitive_and_constant_time_safe():
    payload = {"merchant_id": 7, "order_id": "x"}
    sig = sign(payload, SECRET)
    assert verify(payload, sig, SECRET) is True
    assert verify(payload, sig.upper(), SECRET) is True
    assert verify(payload, "0" * 40, SECRET) is False


def test_verify_rejects_non_string_signature():
    assert verify({"merchant_id": 7}, 12345, SECRET) is False  # type: ignore[arg-type]
    assert verify({"merchant_id": 7}, None, SECRET) is False  # type: ignore[arg-type]
