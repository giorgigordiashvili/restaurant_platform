"""
The only printing entry points other apps call. Every hook is a no-op while
the module is off and never raises: a printer problem must not break an
order status change or a payment.
"""

from __future__ import annotations

import logging

from apps.printing import services

logger = logging.getLogger(__name__)


def on_order_confirmed(order, *, by=None) -> None:
    """The kitchen accepted the order: tickets to every kitchen / bar printer with auto-print on."""
    try:
        if not services.enabled(order.restaurant):
            return
        printers = services.bridge_printers(order.restaurant).filter(auto_print=True)
        services.enqueue_ticket(order, reason="new", by=by, printers=printers)
    except Exception:
        logger.exception("Ticket print failed for order %s", order.pk)


def on_items_added(order, items, *, by=None) -> None:
    """Dishes added to an order the kitchen already has: a ticket with just the new lines."""
    try:
        if not services.enabled(order.restaurant) or order.status not in services.KITCHEN_STATUSES:
            return
        printers = services.bridge_printers(order.restaurant).filter(auto_print=True)
        services.enqueue_ticket(order, reason="added", items=items, by=by, printers=printers)
    except Exception:
        logger.exception("Added-items ticket failed for order %s", order.pk)


def on_payment_completed(payment) -> None:
    """A payment taken at the till: receipt to every receipt printer with auto-print on."""
    try:
        from apps.payments.models import Payment

        order = payment.order
        if order is None or payment.payment_method not in Payment.STAFF_METHODS:
            return
        if not services.enabled(order.restaurant):
            return
        printers = services.bridge_printers(order.restaurant, kinds=("receipt",)).filter(auto_print=True)
        if printers.exists():
            services.enqueue_receipt(order, payment=payment, by=payment.processed_by, printers=printers)
    except Exception:
        logger.exception("Receipt print failed for payment %s", payment.pk)


def on_module_toggled(restaurant, enabled, *, by=None) -> None:
    """Nothing to migrate; queued jobs simply stop being served while off."""
