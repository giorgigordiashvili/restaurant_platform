"""
Everything the platform can tell staff about. Each event names the permission
that decides who hears it by default (the owner always does); staff mute
events they do not want in their preferences.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    code: str
    title: str
    description: str
    resource: str
    action: str
    group: str  # sidebar grouping in the preferences UI
    sound: bool = True  # POS plays the ticket chime


EVENTS = (
    Event("order.new", "New order", "A customer or delivery platform placed an order.", "orders", "update", "Orders"),
    Event("order.cancelled", "Order cancelled", "An accepted order was cancelled.", "orders", "update", "Orders"),
    Event(
        "delivery.cancel_requested",
        "Platform order needs a manual cancel",
        "A Glovo / Wolt order was cancelled here after acceptance; the platform must be told by phone.",
        "orders",
        "update",
        "Orders",
    ),
    Event(
        "delivery.failed",
        "Courier request failed",
        "Wolt Drive / Glovo could not take a delivery; dispatch it another way.",
        "orders",
        "update",
        "Orders",
    ),
    Event(
        "terminal.declined",
        "Card payment declined",
        "A terminal / pay-by-link payment was declined or timed out.",
        "cash",
        "create",
        "Operations",
        False,
    ),
    Event(
        "waitlist.self_joined",
        "Guest joined the waitlist",
        "Someone joined today's queue from the QR at the door.",
        "reservations",
        "update",
        "Reservations",
    ),
    Event(
        "house_account.limit",
        "House account near its limit",
        "An account on credit reached 90% of its limit.",
        "cash",
        "update",
        "Operations",
        False,
    ),
    Event("reservation.new", "New reservation", "A guest booked a table.", "reservations", "update", "Reservations"),
    Event(
        "reservation.cancelled",
        "Reservation cancelled",
        "A guest cancelled a booking.",
        "reservations",
        "update",
        "Reservations",
    ),
    Event(
        "stock.low", "Low stock", "An ingredient dropped below its minimum.", "warehouse", "read", "Warehouse", False
    ),
    Event("stock.out", "Dish sold out", "The warehouse switched a dish off.", "warehouse", "read", "Warehouse"),
    Event(
        "print.failed",
        "Printer failed",
        "A ticket or receipt could not be printed.",
        "settings",
        "update",
        "Operations",
    ),
    Event("shift.closed", "Shift closed", "A cash shift was closed (Z-report).", "cash", "update", "Operations", False),
    Event("review.new", "New review", "A guest left a review.", "settings", "update", "Guests", False),
    Event(
        "purchasing.po_due",
        "Delivery expected",
        "A purchase order is due today.",
        "warehouse",
        "read",
        "Warehouse",
        False,
    ),
    Event(
        "rota.published", "Rota published", "Your shifts for the week are out.", "timekeeping", "read", "Staff", False
    ),
    Event(
        "shift.reminder",
        "Shift starts soon",
        "Your shift starts within the hour.",
        "timekeeping",
        "read",
        "Staff",
        False,
    ),
    Event(
        "campaign.finished", "Campaign sent", "A marketing campaign finished sending.", "crm", "read", "Guests", False
    ),
)
EVENTS_BY_CODE = {e.code: e for e in EVENTS}


def get(code: str) -> Event:
    return EVENTS_BY_CODE[code]


def groups() -> list[tuple[str, list[Event]]]:
    out: dict[str, list[Event]] = {}
    for e in EVENTS:
        out.setdefault(e.group, []).append(e)
    return list(out.items())
