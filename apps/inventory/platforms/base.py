"""
Delivery-platform adapters (Glovo / Wolt / Bolt Food).

When a dish sells out (or comes back) the warehouse asks the adapter of every
platform the restaurant has enabled to flip the dish there. Today only the
manual adapter exists: it leaves the checklist task for a human to tick.
A real API adapter returns ``PlatformResult(ok=True, manual=False)`` and the
task is marked done automatically.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlatformResult:
    ok: bool
    manual: bool = False
    external_ref: str | None = None
    error: str | None = None


class PlatformAdapter:
    code: str = ""

    def set_item_availability(self, link, menu_item, available: bool, *, task=None) -> PlatformResult:
        raise NotImplementedError


class ManualAdapter(PlatformAdapter):
    """No API: a staff member flips the dish in the platform's own app and ticks the task."""

    code = "manual"

    def set_item_availability(self, link, menu_item, available, *, task=None):
        return PlatformResult(ok=True, manual=True)
