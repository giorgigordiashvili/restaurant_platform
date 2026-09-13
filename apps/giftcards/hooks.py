"""Never-raise entry points: order cancelled → money back to the card; online purchase paid → activate."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def on_order_cancelled(order, *, by=None) -> None:
    try:
        from apps.giftcards import services
        from apps.payments.models import Payment

        for p in Payment.objects.filter(
            order=order, payment_method="gift_card", status="completed", gift_card__isnull=False
        ):
            if p.refunds.exists():
                continue
            services.refund_to_card(
                p.gift_card, p.amount, by=by, order=order, note=f"Order {order.order_number} cancelled"
            )
            from apps.payments import services as ledger

            ledger.refund_payment(p, amount=p.amount, by=by, method="gift_card", reason_details="Order cancelled")
    except Exception:
        logger.exception("gift card refund on cancel failed for order %s", order.pk)


def on_online_purchase_paid(card, *, payment=None) -> None:
    try:
        from apps.giftcards import services

        services.activate(card, payment=payment)
    except Exception:
        logger.exception("gift card activation failed for %s", card.pk)
