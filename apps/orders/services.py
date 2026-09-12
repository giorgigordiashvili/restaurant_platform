"""
Order state transitions and order-level money operations.

Every place that moves an order between statuses (POS status endpoint,
payment webhooks) goes through ``transition_order`` so the status history
row and the warehouse hook (reserve -> consume / release) happen exactly
once, in one place.

Discounts, comps, voids, splitting a bill and moving a table live here too:
they all end in ``Order.calculate_totals()`` and an audit row.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.orders.models import Order, OrderDiscount, OrderItem, OrderStatusHistory

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


class OrderError(Exception):
    """A rule refused the operation; ``code`` is stable for API clients."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


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
            # From here on removing a dish is a void, not an edit.
            order.items.exclude(status="cancelled").update(was_sent_to_kitchen=True)
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
        if new_status == "confirmed":
            from apps.printing import hooks as printing_hooks

            printing_hooks.on_order_confirmed(order, by=by)
        elif new_status == "cancelled":
            from apps.fiscal import hooks as fiscal_hooks

            fiscal_hooks.on_order_cancelled(order, by=by)
    return order


# ── guards ────────────────────────────────────────────────────────────────


def _open_for_money_changes(order: Order) -> None:
    if order.status in ("completed", "cancelled"):
        raise OrderError("order_closed", "This order is closed.")


def _refuse_if_paid(order: Order) -> None:
    from apps.payments.services import paid_amount

    if paid_amount(order) > ZERO:
        raise OrderError("already_paid", "Money has already been taken on this order; refund it instead.")


def _reason_ok(reason, reason_text: str, *, manager: bool) -> None:
    if reason is None and not (reason_text or "").strip():
        raise OrderError("reason_required", "A reason is required.")
    if reason is not None and reason.requires_manager and not manager:
        raise OrderError("manager_required", "This reason needs a manager.")


# ── order-level discounts ─────────────────────────────────────────────────


def apply_discount(
    order: Order,
    *,
    mode: str,
    value,
    by,
    reason=None,
    reason_text: str = "",
    kind: str = "manual",
    manager: bool = True,
) -> OrderDiscount:
    _open_for_money_changes(order)
    _refuse_if_paid(order)
    _reason_ok(reason, reason_text, manager=manager)
    value = Decimal(value)
    if value <= ZERO or (mode == "percent" and value > 100):
        raise OrderError("invalid_value", "Discount must be positive (and at most 100%).")
    with transaction.atomic():
        discount = OrderDiscount.objects.create(
            order=order,
            kind=kind,
            mode=mode,
            value=value,
            reason=reason,
            reason_text=(reason_text or "").strip(),
            applied_by=by if getattr(by, "is_authenticated", False) else None,
        )
        order.calculate_totals()
        discount.refresh_from_db(fields=["amount"])
    _audit("order_discount", order, by, f"Discount {discount} on {order.order_number}", discount)
    return discount


def remove_discount(order: Order, discount: OrderDiscount, *, by) -> None:
    _open_for_money_changes(order)
    _refuse_if_paid(order)
    with transaction.atomic():
        discount.delete()
        order.calculate_totals()
    _audit("order_discount", order, by, f"Discount removed from {order.order_number}", order)


def apply_loyalty_tier_discount(order: Order, tier) -> OrderDiscount | None:
    """Replace the order's platform-loyalty discount with the tier's percent (None / 0 removes it)."""
    with transaction.atomic():
        OrderDiscount.objects.filter(order=order, kind="loyalty_tier").delete()
        discount = None
        if tier is not None and tier.discount_percent and tier.discount_percent > 0:
            discount = OrderDiscount.objects.create(
                order=order,
                kind="loyalty_tier",
                mode="percent",
                value=Decimal(tier.discount_percent),
                reason_text=f"Loyalty tier: {getattr(tier, 'name', '')}".strip(": "),
            )
        order.calculate_totals()
    return discount


# ── item-level ────────────────────────────────────────────────────────────


def discount_item(
    item: OrderItem, *, mode: str, value, by, reason=None, reason_text: str = "", manager: bool = True
) -> OrderItem:
    order = item.order
    _open_for_money_changes(order)
    _refuse_if_paid(order)
    _reason_ok(reason, reason_text, manager=manager)
    if item.status == "cancelled":
        raise OrderError("item_voided", "This item was voided.")
    value = Decimal(value)
    if value <= ZERO:
        raise OrderError("invalid_value", "Discount must be positive.")
    if mode == "percent":
        if value > 100:
            raise OrderError("invalid_value", "Percent must be at most 100.")
        amount = (item.total_price * value / Decimal("100")).quantize(Decimal("0.01"))
    else:
        amount = min(value, item.total_price)
    with transaction.atomic():
        item.discount_amount = amount
        item.is_comped = amount >= item.total_price and mode == "percent" and value == 100
        item.discount_reason = reason
        item.discount_reason_text = (reason_text or "").strip()
        item.discounted_by = by if getattr(by, "is_authenticated", False) else None
        item.save(
            update_fields=[
                "discount_amount",
                "is_comped",
                "discount_reason",
                "discount_reason_text",
                "discounted_by",
                "updated_at",
            ]
        )
        order.calculate_totals()
    _audit("order_discount", order, by, f"Item discount {amount} on {item} ({order.order_number})", item)
    return item


def comp_item(item: OrderItem, *, by, reason=None, reason_text: str = "", manager: bool = True) -> OrderItem:
    order = item.order
    _open_for_money_changes(order)
    _refuse_if_paid(order)
    _reason_ok(reason, reason_text, manager=manager)
    if item.status == "cancelled":
        raise OrderError("item_voided", "This item was voided.")
    with transaction.atomic():
        item.discount_amount = item.total_price
        item.is_comped = True
        item.discount_reason = reason
        item.discount_reason_text = (reason_text or "").strip()
        item.discounted_by = by if getattr(by, "is_authenticated", False) else None
        item.save(
            update_fields=[
                "discount_amount",
                "is_comped",
                "discount_reason",
                "discount_reason_text",
                "discounted_by",
                "updated_at",
            ]
        )
        order.calculate_totals()
    _audit("item_comp", order, by, f"Comped {item} on {order.order_number}", item)
    return item


def clear_item_discount(item: OrderItem, *, by) -> OrderItem:
    order = item.order
    _open_for_money_changes(order)
    _refuse_if_paid(order)
    with transaction.atomic():
        item.discount_amount = ZERO
        item.is_comped = False
        item.discount_reason = None
        item.discount_reason_text = ""
        item.discounted_by = None
        item.save(
            update_fields=[
                "discount_amount",
                "is_comped",
                "discount_reason",
                "discount_reason_text",
                "discounted_by",
                "updated_at",
            ]
        )
        order.calculate_totals()
    return item


def void_item(item: OrderItem, *, by, reason=None, reason_text: str = "", manager: bool = True) -> OrderItem:
    """
    Void one line: status -> cancelled with who / why / when; the warehouse
    hook gives the ingredients back; totals are recomputed. Voiding the last
    live item cancels the order.
    """
    order = item.order
    _open_for_money_changes(order)
    _reason_ok(reason, reason_text, manager=manager)
    if item.status == "cancelled":
        return item
    with transaction.atomic():
        item.status = "cancelled"
        item.voided_at = timezone.now()
        item.voided_by = by if getattr(by, "is_authenticated", False) else None
        item.void_reason = reason
        item.void_reason_text = (reason_text or "").strip()
        item.save(update_fields=["status", "voided_at", "voided_by", "void_reason", "void_reason_text", "updated_at"])
        from apps.inventory import hooks as inventory_hooks

        inventory_hooks.on_order_item_cancelled(item, by=by)
        order.calculate_totals()
        remaining = order.items.exclude(status="cancelled").exists()
    label = reason.label if reason is not None else item.void_reason_text
    _audit("item_void", order, by, f"Voided {item} on {order.order_number}: {label}", item)
    if not remaining and order.status not in ("completed", "cancelled"):
        transition_order(
            order, "cancelled", by=by, notes="All items voided", cancellation_reason=f"All items voided: {label}"
        )
    return item


# ── split / move ──────────────────────────────────────────────────────────


def split_items(order: Order, item_ids, *, by, table=None, session=None) -> Order:
    """
    Move some lines to a brand-new order (same table/session unless a target
    is given) so they can be paid separately. Stock reservation lines and
    consumption movements follow the items; the loyalty discount is copied.
    """
    _open_for_money_changes(order)
    _refuse_if_paid(order)
    ids = [str(i) for i in item_ids]
    items = list(order.items.filter(pk__in=ids).exclude(status="cancelled"))
    if not items:
        raise OrderError("no_items", "Pick at least one item to split off.")
    if len(items) >= order.items.exclude(status="cancelled").count():
        raise OrderError("split_all", "Leave at least one item on the original order.")
    with transaction.atomic():
        target_session = session or (table and _active_session(table, by)) or order.table_session
        target_table = table or (target_session.table if target_session else order.table)
        new_order = Order.objects.create(
            restaurant=order.restaurant,
            table=target_table,
            table_session=target_session,
            session_guest=order.session_guest if target_session == order.table_session else None,
            reservation=order.reservation,
            customer=order.customer,
            order_type=order.order_type,
            status=order.status,
            customer_name=order.customer_name,
            customer_phone=order.customer_phone,
            customer_email=order.customer_email,
            delivery_address=order.delivery_address,
            server=order.server,
            handled_by=by if getattr(by, "is_authenticated", False) else order.handled_by,
            confirmed_at=order.confirmed_at,
            estimated_ready_at=order.estimated_ready_at,
        )
        OrderItem.objects.filter(pk__in=[i.pk for i in items]).update(order=new_order)
        _move_stock_records(order, new_order, [i.pk for i in items])
        for d in order.discounts.filter(kind="loyalty_tier"):
            OrderDiscount.objects.create(
                order=new_order, kind=d.kind, mode=d.mode, value=d.value, reason=d.reason, reason_text=d.reason_text
            )
        order.calculate_totals()
        new_order.calculate_totals()
        who = by if getattr(by, "is_authenticated", False) else None
        OrderStatusHistory.objects.create(
            order=new_order,
            from_status="",
            to_status=new_order.status,
            changed_by=who,
            notes=f"Split from {order.order_number}",
        )
        OrderStatusHistory.objects.create(
            order=order,
            from_status=order.status,
            to_status=order.status,
            changed_by=who,
            notes=f"{len(items)} item(s) split to {new_order.order_number}",
        )
    _audit(
        "order_split",
        order,
        by,
        f"{len(items)} item(s) split from {order.order_number} to {new_order.order_number}",
        new_order,
        {"items": ids, "new_order": new_order.order_number},
    )
    return new_order


def _move_stock_records(source: Order, target: Order, item_ids) -> None:
    """Reservation lines / consumption movements follow the OrderItem rows."""
    try:
        from apps.inventory.models import OrderStockReservation, OrderStockReservationLine, StockMovement

        res = OrderStockReservation.objects.filter(order=source).first()
        if res is not None:
            new_res, _ = OrderStockReservation.objects.get_or_create(
                order=target,
                defaults={
                    "restaurant": res.restaurant,
                    "status": res.status,
                    "strict": res.strict,
                    "consumed_at": res.consumed_at,
                    "released_at": res.released_at,
                },
            )
            OrderStockReservationLine.objects.filter(reservation=res, order_item_id__in=item_ids).update(
                reservation=new_res
            )
        StockMovement.objects.filter(order=source, order_item_id__in=item_ids).update(order=target)
    except Exception:  # pragma: no cover - never lose a split over stock bookkeeping
        logger.exception("Could not move stock records from %s to %s", source.pk, target.pk)


def _active_session(table, by):
    from apps.tables.models import TableSession, TableSessionGuest

    session = table.sessions.filter(status="active").first()
    if session is None:
        session = TableSession.objects.create(
            table=table, host=by if getattr(by, "is_authenticated", False) else None, guest_count=1
        )
        if session.host_id:
            TableSessionGuest.objects.create(session=session, user=session.host, is_host=True)
        table.set_occupied()
    return session


def move_order(order: Order, table, *, by) -> Order:
    """Re-seat an order at another table (creating/using that table's active session; closing an emptied source)."""
    _open_for_money_changes(order)
    if table.restaurant_id != order.restaurant_id:
        raise OrderError("table_not_found", "Table not found.")
    if order.table_id == table.pk:
        return order
    with transaction.atomic():
        source_session = order.table_session
        target_session = _active_session(table, by)
        old_table = order.table
        order.table = table
        order.table_session = target_session
        order.session_guest = None
        order.save(update_fields=["table", "table_session", "session_guest", "updated_at"])
        if source_session and source_session.status == "active":
            if not source_session.orders.exclude(status="cancelled").exists():
                source_session.close()
        OrderStatusHistory.objects.create(
            order=order,
            from_status=order.status,
            to_status=order.status,
            changed_by=by if getattr(by, "is_authenticated", False) else None,
            notes=f"Moved from table {old_table.number if old_table else '-'} to {table.number}",
        )
    _audit("order_move", order, by, f"{order.order_number} moved to table {table.number}", order)
    return order


# ── helpers ───────────────────────────────────────────────────────────────


def _audit(action, order, user, description, target=None, changes=None):
    try:
        from apps.audit.services import log_action

        log_action(
            action,
            user=user if getattr(user, "is_authenticated", False) else None,
            restaurant=order.restaurant,
            description=description,
            target_model=type(target).__name__ if target is not None else "Order",
            target_id=str(target.pk) if target is not None else str(order.pk),
            changes=changes or {},
        )
    except Exception:  # pragma: no cover
        logger.exception("Order audit failed: %s", description)
