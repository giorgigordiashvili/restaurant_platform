from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def on_module_toggled(restaurant, enabled, *, by=None) -> None:
    if not enabled:
        return
    try:
        from apps.waitlist import services

        services.settings_for(restaurant)
    except Exception:
        logger.exception("waitlist module hook failed for %s", restaurant.slug)
