"""
The money ledger.

Every way an order gets paid -- cash at the POS, a card terminal, a BOG or
Flitt webhook, a whole-table settle -- ends in :func:`record_payment`, which
creates one ``Payment`` plus its ``PaymentAllocation`` rows. "Is this order
paid?" is then a question about allocations (:func:`paid_amount`,
:func:`is_paid`, :func:`unpaid_orders`), never about provider transactions.

Cash shifts (:func:`open_shift` / :func:`close_shift`) reconcile the drawer:
while the ``cash`` module is on, taking cash requires an open shift and every
payment is attached to it, so the Z report explains the cash count.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Count, F, Max, Sum
from django.utils import timezone

from apps.payments.models import (
    CashMovement,
    CashShift,
    DiscountReason,
    Payment,
    PaymentAllocation,
    ReceiptSequence,
    Refund,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
CENT = Decimal("0.01")

PAID_STATUSES = ("completed", "partially_refunded", "refunded")

# Built-in reasons offered when a restaurant has not defined its own.
DEFAULT_REASONS = {
    "discount": ["Regular guest", "Staff / friends", "Manager's discretion", "Service recovery"],
    "comp": ["Complaint", "Long wait", "Wrong dish", "Owner's treat"],
    "void": ["Guest changed mind", "Entered by mistake", "Out of ingredients", "Kitchen error"],
    "refund": ["Customer request", "Quality issue", "Wrong order", "Order cancelled"],
}


def q(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


class LedgerError(Exception):
    """A ledger rule refused the operation. ``code`` is stable for API clients."""

    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


class ShiftAlreadyOpen(LedgerError):
    def __init__(self, shift):
        super().__init__("shift_open", f"Shift #{shift.number} is already open.", shift_id=str(shift.pk))


# ── counters ──────────────────────────────────────────────────────────────


def next_number(restaurant_id, kind: str, period: str = "") -> int:
    """Next value of a locked counter. Safe to call inside or outside a transaction."""
    with transaction.atomic():
        seq, _ = ReceiptSequence.objects.get_or_create(restaurant_id=restaurant_id, kind=kind, period=period)
        seq = ReceiptSequence.objects.select_for_update().get(pk=seq.pk)
        seq.last = F("last") + 1
        seq.save(update_fields=["last"])
        seq.refresh_from_db(fields=["last"])
        return seq.last


def next_receipt_number(restaurant_id) -> str:
    """``RCP-YYMMDD-NNNN``, restarting every day, race-free."""
    period = timezone.localdate().strftime("%y%m%d")
    n = next_number(restaurant_id, "payment_daily", period)
    return f"RCP-{period}-{n:04d}"


# ── shifts ────────────────────────────────────────────────────────────────


def shifts_required(restaurant) -> bool:
    from apps.core import modules

    return modules.is_enabled(restaurant, "cash")


def current_shift(restaurant, *, lock: bool = False) -> CashShift | None:
    qs = CashShift.objects.filter(restaurant=restaurant, status="open")
    if lock:
        qs = qs.select_for_update()
    return qs.first()


def open_shift(restaurant, *, by, opening_float=ZERO, register: str = "", notes: str = "") -> CashShift:
    from apps.tenants.models import Restaurant

    with transaction.atomic():
        Restaurant.objects.select_for_update().get(pk=restaurant.pk)  # serialises openers
        existing = current_shift(restaurant)
        if existing:
            raise ShiftAlreadyOpen(existing)
        last = CashShift.objects.filter(restaurant=restaurant).aggregate(n=Max("number"))["n"] or 0
        shift = CashShift.objects.create(
            restaurant=restaurant,
            number=last + 1,
            register=register or "",
            opened_by=by if getattr(by, "is_authenticated", False) else None,
            opened_at=timezone.now(),
            opening_float=q(opening_float),
            notes=notes or "",
        )
    _audit("shift_open", restaurant, by, f"Shift #{shift.number} opened with float {shift.opening_float}", shift)
    return shift


def add_movement(shift: CashShift, *, kind: str, amount, reason: str, by) -> CashMovement:
    if shift.status != "open":
        raise LedgerError("shift_closed", "This shift is closed.")
    amount = q(amount)
    if amount <= 0:
        raise LedgerError("invalid_amount", "Amount must be greater than zero.")
    if kind not in ("paid_in", "paid_out"):
        raise LedgerError("invalid_kind", "Kind must be paid_in or paid_out.")
    if not (reason or "").strip():
        raise LedgerError("reason_required", "A reason is required.")
    movement = CashMovement.objects.create(
        shift=shift,
        kind=kind,
        amount=amount,
        reason=reason.strip(),
        created_by=by if getattr(by, "is_authenticated", False) else None,
    )
    _audit(
        "cash_movement",
        shift.restaurant,
        by,
        f"{movement.get_kind_display()} {amount}: {movement.reason}",
        movement,
        {"kind": kind, "amount": str(amount), "shift": str(shift.pk)},
    )
    return movement


def build_report(shift: CashShift, until=None) -> dict:
    """
    Figures for the X (live) and Z (closing) report. Everything is a string
    so the dict can be frozen into ``CashShift.report`` as-is.
    """
    from apps.orders.models import OrderDiscount, OrderItem

    until = until or timezone.now()
    payments = Payment.objects.filter(shift=shift, status__in=PAID_STATUSES, completed_at__lte=until)
    refunds = Refund.objects.filter(shift=shift, status="completed", completed_at__lte=until)
    movements = CashMovement.objects.filter(shift=shift, created_at__lte=until)

    by_method = {}
    for row in payments.values("payment_method").annotate(
        count=Count("id"), amount=Sum("amount"), tips=Sum("tip_amount"), change=Sum("change_given")
    ):
        by_method[row["payment_method"]] = {
            "count": row["count"],
            "amount": str(q(row["amount"])),
            "tips": str(q(row["tips"])),
            "total": str(q((row["amount"] or ZERO) + (row["tips"] or ZERO))),
        }
    refunds_by_method = {}
    for row in refunds.values("method").annotate(count=Count("id"), amount=Sum("amount")):
        refunds_by_method[row["method"]] = {"count": row["count"], "amount": str(q(row["amount"]))}

    paid_in = q(movements.filter(kind="paid_in").aggregate(s=Sum("amount"))["s"])
    paid_out = q(movements.filter(kind="paid_out").aggregate(s=Sum("amount"))["s"])

    cash = by_method.get("cash", {})
    cash_sales = Decimal(cash.get("amount", "0"))
    cash_tips = Decimal(cash.get("tips", "0"))
    cash_refunds = Decimal(refunds_by_method.get("cash", {}).get("amount", "0"))
    expected_cash = q(shift.opening_float + cash_sales + cash_tips + paid_in - paid_out - cash_refunds)

    order_ids = set(PaymentAllocation.objects.filter(payment__in=payments).values_list("order_id", flat=True))
    items = OrderItem.objects.filter(order__restaurant=shift.restaurant)
    voids = items.filter(
        status="cancelled", voided_at__isnull=False, voided_at__gte=shift.opened_at, voided_at__lte=until
    )
    comps = items.filter(is_comped=True, order_id__in=order_ids)
    item_discounts = items.filter(is_comped=False, discount_amount__gt=0, order_id__in=order_ids)
    order_discounts = OrderDiscount.objects.filter(order_id__in=order_ids)
    voids_after_kitchen = voids.filter(was_sent_to_kitchen=True)

    sales = q(payments.aggregate(s=Sum("amount"))["s"])
    tips = q(payments.aggregate(s=Sum("tip_amount"))["s"])
    refunded = q(refunds.aggregate(s=Sum("amount"))["s"])

    return {
        "shift_number": shift.number,
        "opened_at": shift.opened_at.isoformat(),
        "opened_by": _user_label(shift.opened_by),
        "until": until.isoformat(),
        "opening_float": str(q(shift.opening_float)),
        "sales": str(sales),
        "tips": str(tips),
        "refunds": str(refunded),
        "net_sales": str(q(sales - refunded)),
        "payments_count": payments.count(),
        "orders_count": len(order_ids),
        "by_method": by_method,
        "refunds_by_method": refunds_by_method,
        "paid_in": str(paid_in),
        "paid_out": str(paid_out),
        "movements": [
            {
                "kind": m.kind,
                "amount": str(q(m.amount)),
                "reason": m.reason,
                "by": _user_label(m.created_by),
                "at": m.created_at.isoformat(),
            }
            for m in movements.select_related("created_by")
        ],
        "cash_sales": str(q(cash_sales)),
        "cash_tips": str(q(cash_tips)),
        "cash_refunds": str(q(cash_refunds)),
        "expected_cash": str(expected_cash),
        "discounts": {
            "orders_count": order_discounts.count(),
            "orders_amount": str(q(order_discounts.aggregate(s=Sum("amount"))["s"])),
            "items_count": item_discounts.count(),
            "items_amount": str(q(item_discounts.aggregate(s=Sum("discount_amount"))["s"])),
        },
        "comps": {"count": comps.count(), "amount": str(q(comps.aggregate(s=Sum("total_price"))["s"]))},
        "voids": {
            "count": voids.count(),
            "amount": str(q(voids.aggregate(s=Sum("total_price"))["s"])),
            "after_kitchen_count": voids_after_kitchen.count(),
            "after_kitchen_amount": str(q(voids_after_kitchen.aggregate(s=Sum("total_price"))["s"])),
        },
    }


def x_report(shift: CashShift) -> dict:
    return build_report(shift)


def close_shift(shift: CashShift, *, by, counted_cash, notes: str = "") -> CashShift:
    with transaction.atomic():
        shift = CashShift.objects.select_for_update().get(pk=shift.pk)
        if shift.status != "open":
            raise LedgerError("shift_closed", "This shift is already closed.")
        now = timezone.now()
        report = build_report(shift, until=now)
        shift.status = "closed"
        shift.closed_at = now
        shift.closed_by = by if getattr(by, "is_authenticated", False) else None
        shift.counted_cash = q(counted_cash)
        shift.expected_cash = Decimal(report["expected_cash"])
        shift.difference = q(shift.counted_cash - shift.expected_cash)
        report["counted_cash"] = str(shift.counted_cash)
        report["difference"] = str(shift.difference)
        report["closed_at"] = now.isoformat()
        report["closed_by"] = _user_label(shift.closed_by)
        shift.report = report
        if notes:
            shift.notes = (shift.notes + "\n" if shift.notes else "") + notes
        shift.save()
    _audit(
        "shift_close",
        shift.restaurant,
        by,
        f"Shift #{shift.number} closed: counted {shift.counted_cash}, expected {shift.expected_cash}",
        shift,
        {"counted": str(shift.counted_cash), "expected": str(shift.expected_cash), "difference": str(shift.difference)},
    )
    from apps.notifications import hooks as notification_hooks

    notification_hooks.on_shift_closed(shift, by=by)
    return shift


# ── paid / unpaid ─────────────────────────────────────────────────────────


def paid_amount(order) -> Decimal:
    """Money allocated to the order from completed payments, minus refunds taken off it."""
    paid = PaymentAllocation.objects.filter(order=order, payment__status__in=PAID_STATUSES).aggregate(s=Sum("amount"))[
        "s"
    ]
    refunded = Refund.objects.filter(order=order, status="completed").aggregate(s=Sum("amount"))["s"]
    return q((paid or ZERO) - (refunded or ZERO))


def balance(order) -> Decimal:
    return max(q(order.total or ZERO) - paid_amount(order), ZERO)


def is_paid(order) -> bool:
    if order.status == "cancelled":
        return True
    return balance(order) <= ZERO


def annotate_paid(orders) -> dict:
    """{order_id: paid_amount} for a batch of orders in two queries."""
    ids = [o.pk for o in orders]
    paid = {
        row["order_id"]: row["s"]
        for row in PaymentAllocation.objects.filter(order_id__in=ids, payment__status__in=PAID_STATUSES)
        .values("order_id")
        .annotate(s=Sum("amount"))
    }
    refunded = {
        row["order_id"]: row["s"]
        for row in Refund.objects.filter(order_id__in=ids, status="completed")
        .values("order_id")
        .annotate(s=Sum("amount"))
    }
    return {pk: q((paid.get(pk) or ZERO) - (refunded.get(pk) or ZERO)) for pk in ids}


def unpaid_orders(session, *, lock: bool = False) -> list:
    """Live orders on a table session that still carry a balance, oldest first."""
    qs = session.orders.exclude(status="cancelled").order_by("created_at")
    if lock:
        qs = qs.select_for_update()
    orders = list(qs)
    paid = annotate_paid(orders)
    return [o for o in orders if q(o.total or ZERO) - paid[o.pk] > ZERO]


def session_balance(session) -> Decimal:
    orders = list(session.orders.exclude(status="cancelled"))
    paid = annotate_paid(orders)
    return q(sum((max(q(o.total or ZERO) - paid[o.pk], ZERO) for o in orders), ZERO))


def split_evenly(total, n: int) -> list[Decimal]:
    """``n`` shares of ``total`` that add up exactly; the remainder cents go on the first shares."""
    total = q(total)
    n = int(n)
    if n <= 0:
        raise LedgerError("invalid_split", "Number of shares must be at least 1.")
    cents = int(total * 100)
    base, extra = divmod(cents, n)
    return [Decimal(base + (1 if i < extra else 0)) / 100 for i in range(n)]


# ── recording payments ────────────────────────────────────────────────────


def record_payment(
    restaurant,
    *,
    method: str,
    amount,
    by=None,
    order=None,
    session=None,
    orders=None,
    tip=ZERO,
    tendered=None,
    external_id: str = "",
    allow_overpay: bool = False,
    notes: str = "",
    customer=None,
    currency: str | None = None,
    request=None,
) -> Payment:
    """
    The single settlement entry point.

    * ``order`` / ``orders`` / ``session`` say what is being paid (a session
      means "every unpaid order on the table").
    * Idempotent on ``external_id`` (provider webhooks are retried).
    * Staff paths refuse to overpay (409 ``already_paid`` / ``overpay``);
      webhooks pass ``allow_overpay=True`` because the money has already moved.
    * Cash needs an open shift while the ``cash`` module is on.
    * Allocations are greedy, oldest order first.
    """
    from apps.orders.models import Order

    amount = q(amount)
    tip = q(tip)
    if external_id:
        existing = Payment.objects.filter(external_payment_id=external_id).first()
        if existing:
            return existing
    if method not in dict(Payment.PAYMENT_METHOD_CHOICES):
        raise LedgerError("invalid_method", f"Unknown payment method '{method}'.")
    if amount <= ZERO:
        raise LedgerError("invalid_amount", "Amount must be greater than zero.")
    if tendered is not None:
        tendered = q(tendered)
        if tendered < amount + tip:
            raise LedgerError("insufficient_tendered", "Cash tendered is less than the amount due.")

    with transaction.atomic():
        if orders:
            target_ids = [o.pk for o in orders]
        elif order is not None:
            target_ids = [order.pk]
        elif session is not None:
            target_ids = [o.pk for o in unpaid_orders(session, lock=True)]
            if not target_ids:
                raise LedgerError("nothing_to_pay", "No unpaid orders on this table.")
        else:
            raise LedgerError("no_target", "Give an order, several orders or a table session.")

        targets = list(
            Order.objects.select_for_update().filter(pk__in=target_ids, restaurant=restaurant).order_by("created_at")
        )
        if len(targets) != len(set(target_ids)):
            raise LedgerError("order_not_found", "One of the orders does not belong to this restaurant.")
        targets = [o for o in targets if o.status != "cancelled"]
        if not targets:
            raise LedgerError("nothing_to_pay", "The order is cancelled.")

        paid_before = annotate_paid(targets)
        balances = {o.pk: max(q(o.total or ZERO) - paid_before[o.pk], ZERO) for o in targets}
        total_balance = q(sum(balances.values(), ZERO))
        if not allow_overpay and amount > total_balance:
            code = "already_paid" if total_balance <= ZERO else "overpay"
            raise LedgerError(
                code,
                "This order is already paid." if code == "already_paid" else "Amount exceeds the balance due.",
                balance=str(total_balance),
            )

        if method == "cash" and shifts_required(restaurant):
            shift = current_shift(restaurant, lock=True)
            if shift is None:
                raise LedgerError("shift_required", "Open a cash shift before taking cash.")
        else:
            shift = current_shift(restaurant)

        change = q(tendered - amount - tip) if tendered is not None else ZERO
        first = targets[0]
        payment = Payment.objects.create(
            restaurant=restaurant,
            order=first,
            session=session or first.table_session,
            shift=shift,
            customer=customer or first.customer,
            processed_by=by if getattr(by, "is_authenticated", False) else None,
            amount=amount,
            tip_amount=tip,
            payment_method=method,
            status="pending",
            external_payment_id=external_id or "",
            tendered=tendered,
            change_given=change,
            currency=currency or getattr(restaurant, "default_currency", None) or "GEL",
            notes=notes or "",
        )

        remaining = amount
        allocations = []
        for o in targets:
            share = min(balances[o.pk], remaining)
            if share > ZERO:
                allocations.append(PaymentAllocation(payment=payment, order=o, amount=share))
                remaining -= share
        if remaining > ZERO:
            # Overpay (webhook amounts include things like a customer tip that
            # never reached order.total): book the rest against the last order.
            last = targets[-1]
            for a in allocations:
                if a.order_id == last.pk:
                    a.amount += remaining
                    break
            else:
                allocations.append(PaymentAllocation(payment=payment, order=last, amount=remaining))
        PaymentAllocation.objects.bulk_create(allocations)

        payment.complete()

        newly_paid = [o for o in targets if balances[o.pk] > ZERO and balances[o.pk] <= amount_for(allocations, o)]
        for o in newly_paid:
            _accrue_loyalty(o, method)

    try:
        from apps.printing import hooks as printing_hooks

        printing_hooks.on_payment_completed(payment)
    except Exception:  # pragma: no cover - printing must never break a payment
        logger.exception("Receipt print hook failed for payment %s", payment.pk)

    _audit(
        "payment_collect",
        restaurant,
        by,
        f"{payment.get_payment_method_display()} payment {payment.total_amount} ({payment.receipt_number})",
        payment,
        {
            "method": method,
            "amount": str(amount),
            "tip": str(tip),
            "orders": [o.order_number for o in targets],
            "change": str(change),
        },
        request=request,
    )
    return payment


def amount_for(allocations, order) -> Decimal:
    return q(sum((a.amount for a in allocations if a.order_id == order.pk), ZERO))


def _accrue_loyalty(order, method: str) -> None:
    """Platform loyalty points once per fully paid order (idempotent on the ledger)."""
    try:
        from apps.loyalty.models import PlatformLoyaltyLedger
        from apps.loyalty.services import accrue_platform_points

        if PlatformLoyaltyLedger.objects.filter(order=order).exists():
            return
        accrue_platform_points(
            order, source="cash" if method in Payment.STAFF_METHODS else method.replace("online_", "")
        )
    except Exception:  # pragma: no cover - loyalty must never break a payment
        logger.exception("Loyalty accrual failed for order %s", order.pk)


# ── refunds ───────────────────────────────────────────────────────────────


def refund_payment(
    payment: Payment,
    *,
    amount,
    by,
    method: str | None = None,
    reason: str = "customer_request",
    reason_details: str = "",
    reason_code: DiscountReason | None = None,
    order=None,
    request=None,
) -> Refund:
    amount = q(amount)
    if amount <= ZERO:
        raise LedgerError("invalid_amount", "Amount must be greater than zero.")
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if not payment.is_refundable:
            raise LedgerError("not_refundable", "This payment cannot be refunded.")
        if amount > payment.refundable_amount:
            raise LedgerError(
                "exceeds_refundable",
                f"Cannot refund more than {payment.refundable_amount}.",
                refundable=str(payment.refundable_amount),
            )
        if method is None:
            method = (
                "cash"
                if payment.payment_method == "cash"
                else ("card_terminal" if payment.payment_method == "card_terminal" else "online")
            )
        restaurant = payment.restaurant
        shift = current_shift(restaurant, lock=(method == "cash"))
        if method == "cash" and shifts_required(restaurant) and shift is None:
            raise LedgerError("shift_required", "Open a cash shift before refunding cash.")
        if order is None and payment.allocations.count() == 1:
            order = payment.allocations.first().order
        refund = Refund.objects.create(
            payment=payment,
            restaurant=restaurant,
            order=order,
            shift=shift,
            method=method,
            processed_by=by if getattr(by, "is_authenticated", False) else None,
            amount=amount,
            reason=reason,
            reason_details=reason_details or (reason_code.label if reason_code else ""),
            reason_code=reason_code,
            status="pending",
        )
        refund.complete()
    _audit(
        "refund",
        restaurant,
        by,
        f"Refund {amount} on {payment.receipt_number or payment.pk}: {refund.reason_details or refund.reason}",
        refund,
        {"amount": str(amount), "method": method, "payment": str(payment.pk)},
        request=request,
    )
    return refund


# ── reasons ───────────────────────────────────────────────────────────────


def reasons_for(restaurant, kind: str) -> list[dict]:
    """Active reasons of a kind; the built-in list when the restaurant defined none."""
    rows = list(DiscountReason.objects.filter(restaurant=restaurant, kind=kind, is_active=True))
    if rows:
        return [{"id": str(r.pk), "label": r.label, "requires_manager": r.requires_manager, "kind": kind} for r in rows]
    return [
        {"id": None, "label": label, "requires_manager": False, "kind": kind} for label in DEFAULT_REASONS.get(kind, [])
    ]


# ── helpers ───────────────────────────────────────────────────────────────


def _user_label(user) -> str:
    if not user:
        return ""
    name = user.get_full_name() if hasattr(user, "get_full_name") else ""
    return name or getattr(user, "email", "") or str(user.pk)


def _audit(action, restaurant, user, description, target=None, changes=None, request=None):
    try:
        from apps.audit.services import log_action

        log_action(
            action,
            request=request,
            user=user if getattr(user, "is_authenticated", False) else None,
            restaurant=restaurant,
            description=description,
            target_model=type(target).__name__ if target is not None else "",
            target_id=str(target.pk) if target is not None else "",
            changes=changes or {},
        )
    except Exception:  # pragma: no cover - auditing must never break the ledger
        logger.exception("Ledger audit failed: %s", description)
