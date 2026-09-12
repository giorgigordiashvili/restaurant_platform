"""
Order state transitions.

Every place that moves an order between statuses (POS status endpoint,
payment webhooks) goes through ``transition_order`` so the status history
row and the warehouse hook (reserve -> consume / release) happen exactly
once, in one place.
"""

from __future__ import annotations

from django.db import transaction

from apps.orders.models import Order, OrderStatusHistory


def transition_order(
    order: Order,
    new_status: str,
    *,
    by=None,
    notes: str = "",
    estimated_minutes: int | None = None,
    cancellation_reason: str = "",
) -> Order:
    old_status = order.status
    with transaction.atomic():
        if new_status == "confirmed":
            order.confirm(estimated_minutes)
        elif new_status == "cancelled":
            order.cancel(cancellation_reason)
        elif new_status == "completed":
            order.complete()
        else:
            order.status = new_status
            order.save(update_fields=["status", "updated_at"])

        OrderStatusHistory.objects.create(
            order=order,
            from_status=old_status,
            to_status=new_status,
            changed_by=by if getattr(by, "is_authenticated", False) else None,
            notes=notes,
        )

    if old_status != new_status:
        from apps.inventory import hooks

        hooks.on_order_status_changed(order, old_status, new_status, by=by)
    return order
