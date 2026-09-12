"""
Ids we hand to every platform: ``p<uuid>`` dish, ``m<uuid>`` modifier,
``g<uuid>`` modifier group. Inbound payloads map back with the ``parse_*``
helpers (UUID-validated, so ids we never issued give ``None``).
"""

from __future__ import annotations

import uuid


def product_id(menu_item) -> str:
    return f"p{menu_item.pk}"


def attribute_id(modifier) -> str:
    return f"m{modifier.pk}"


def group_id(group) -> str:
    return f"g{group.pk}"


def _uuid_after(prefix: str, value) -> str | None:
    s = str(value or "")
    if not s.startswith(prefix) or len(s) <= len(prefix):
        return None
    try:
        return str(uuid.UUID(s[len(prefix) :]))
    except ValueError:
        return None


def parse_product_id(value) -> str | None:
    """'p<uuid>' -> uuid string (None for anything else, incl. ids we never issued)."""
    return _uuid_after("p", value)


def parse_attribute_id(value) -> str | None:
    return _uuid_after("m", value)


def parse_group_id(value) -> str | None:
    return _uuid_after("g", value)
