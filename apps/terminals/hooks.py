"""Module toggle: give every restaurant a 'physical terminal' row so the POS has something to pick."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def on_module_toggled(restaurant, enabled, *, by=None) -> None:
    if not enabled:
        return
    try:
        from apps.terminals.models import PaymentTerminal

        if not PaymentTerminal.objects.filter(restaurant=restaurant).exists():
            PaymentTerminal.objects.create(
                restaurant=restaurant, name="Card terminal", provider="manual", is_default=True
            )
    except Exception:
        logger.exception("terminals module hook failed for %s", restaurant.slug)
