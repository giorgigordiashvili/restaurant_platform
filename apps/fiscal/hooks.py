"""
Entry points other apps call. Every hook is a no-op while the module is
off and never raises: a fiscal hiccup must not break a payment or a
kitchen order.
"""

from __future__ import annotations

import logging

from apps.fiscal import services

logger = logging.getLogger(__name__)


def on_payment_completed(payment) -> None:
    try:
        services.create_receipt(payment)
    except Exception:
        logger.exception("Fiscal receipt failed for payment %s", payment.pk)


def on_refund_completed(refund) -> None:
    try:
        restaurant = refund.restaurant or refund.payment.restaurant
        if not services.enabled(restaurant):
            return
        from apps.fiscal.models import FiscalDocument

        receipt = (
            FiscalDocument.objects.filter(payment=refund.payment, kind="receipt").exclude(status="cancelled").first()
        )
        services.create_refund_document(
            restaurant=restaurant, amount=refund.amount, refund=refund, order=refund.order, reverses=receipt
        )
    except Exception:
        logger.exception("Fiscal refund document failed for refund %s", refund.pk)


def on_order_cancelled(order, *, by=None) -> None:
    """Receipts already confirmed for a cancelled order get a reversing refund document."""
    try:
        if not services.enabled(order.restaurant):
            return
        from django.db.models import Sum

        from apps.fiscal.models import FiscalDocument

        for receipt in FiscalDocument.objects.filter(order=order, kind="receipt", status="confirmed"):
            already = (
                receipt.reversed_by.filter(status__in=("queued", "sent", "confirmed")).aggregate(s=Sum("gross_total"))[
                    "s"
                ]
                or 0
            )
            remaining = receipt.gross_total - already
            if remaining > 0:
                services.create_refund_document(
                    restaurant=order.restaurant, amount=remaining, order=order, reverses=receipt, by=by
                )
    except Exception:
        logger.exception("Fiscal reversal failed for order %s", order.pk)


def on_stock_received(lot) -> None:
    try:
        services.create_inbound_waybill(lot)
    except Exception:
        logger.exception("Inbound waybill failed for lot %s", lot.pk)


def on_module_toggled(restaurant, enabled, *, by=None) -> None:
    if enabled:
        try:
            services.profile_for(restaurant)
        except Exception:
            logger.exception("Could not create the fiscal profile for %s", restaurant.slug)
