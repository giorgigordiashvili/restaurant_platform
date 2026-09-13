"""
Terminal transactions: start a sale, follow it (callback / poll / bridge /
cashier confirm), and book the approved ones through ``record_payment``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.payments import services as ledger
from apps.payments.models import Payment
from apps.terminals.models import PaymentTerminal, TerminalTransaction
from apps.terminals.providers import registry
from apps.terminals.providers.base import Result, TerminalError

logger = logging.getLogger(__name__)
ZERO = Decimal("0.00")


class TerminalServiceError(Exception):
    def __init__(self, code: str, message: str = "", **extra):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.extra = extra


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "terminals_enabled", False))


def q(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def ledger_method(terminal: PaymentTerminal) -> str:
    return {"bog_link": "online_bog", "tbc_tpay": "online_tbc"}.get(terminal.provider, "card_terminal")


def terminals_for(restaurant, *, active_only=True):
    qs = PaymentTerminal.objects.filter(restaurant=restaurant)
    return qs.filter(is_active=True) if active_only else qs


# ── starting ───────────────────────────────────────────────────────────────


def _targets(restaurant, *, order=None, session=None, orders=None):
    from apps.orders.models import Order

    if orders:
        rows = list(Order.objects.filter(pk__in=[o.pk for o in orders], restaurant=restaurant))
        if len(rows) != len(orders):
            raise TerminalServiceError("order_not_found", "One of the orders does not belong to this restaurant.")
        return rows
    if order is not None:
        if order.restaurant_id != restaurant.pk:
            raise TerminalServiceError("order_not_found", "Order not found.")
        return [order]
    if session is not None:
        rows = ledger.unpaid_orders(session)
        if not rows:
            raise TerminalServiceError("nothing_to_pay", "No unpaid orders on this table.")
        return rows
    raise TerminalServiceError("no_target", "Give an order, several orders or a table session.")


def start_sale(
    restaurant,
    terminal: PaymentTerminal,
    amount,
    *,
    order=None,
    session=None,
    orders=None,
    tip=ZERO,
    by=None,
    send_to: str = "",
    session_http=None,
) -> TerminalTransaction:
    """Open a transaction and hand it to the provider. One open transaction per order / table."""
    if terminal.restaurant_id != restaurant.pk or not terminal.is_active:
        raise TerminalServiceError("terminal_not_found", "Terminal not found.")
    if not registry.is_configured(terminal):
        raise TerminalServiceError("not_configured", "This terminal is not set up yet.")
    amount = q(amount)
    tip = q(tip)
    if amount <= ZERO:
        raise TerminalServiceError("invalid_amount", "Amount must be greater than zero.")
    targets = _targets(restaurant, order=order, session=session, orders=orders)
    paid = ledger.annotate_paid(targets)
    balance = q(sum((max(q(o.total or ZERO) - paid[o.pk], ZERO) for o in targets), ZERO))
    if amount > balance:
        raise TerminalServiceError(
            "overpay" if balance > ZERO else "already_paid",
            "Amount exceeds the balance due." if balance > ZERO else "This order is already paid.",
            balance=str(balance),
        )
    open_qs = TerminalTransaction.objects.filter(restaurant=restaurant, status__in=TerminalTransaction.OPEN)
    if session is not None:
        busy = open_qs.filter(session=session).exists()
    else:
        busy = (
            open_qs.filter(order__in=targets).exists()
            or open_qs.filter(order_ids__overlap=[str(o.pk) for o in targets]).exists()
        )
    if busy:
        raise TerminalServiceError("terminal_busy", "A card payment for this bill is already in progress.")

    tx = TerminalTransaction.objects.create(
        restaurant=restaurant,
        terminal=terminal,
        kind="sale",
        amount=amount,
        tip=tip,
        currency=getattr(restaurant, "default_currency", "GEL") or "GEL",
        status="pending",
        order=order if order is not None else (targets[0] if len(targets) == 1 and session is None else None),
        session=session,
        order_ids=[str(o.pk) for o in targets] if orders else [],
        initiated_by=by if getattr(by, "is_authenticated", False) else None,
        sent_to=send_to or "",
        expires_at=timezone.now() + timedelta(seconds=int(terminal.timeout_seconds or 180)),
    )
    try:
        provider = registry.get_provider(terminal, session=session_http)
        started = provider.start(tx)
    except TerminalError as exc:
        tx.status = "failed"
        tx.error = str(exc)[:300]
        tx.response = exc.payload if isinstance(exc.payload, dict) else {}
        tx.finished_at = timezone.now()
        tx.save(update_fields=["status", "error", "response", "finished_at", "updated_at"])
        raise TerminalServiceError("provider_failed", str(exc)) from exc
    tx.status = started.status or "sent"
    tx.external_id = started.external_id or ""
    tx.pay_url = started.pay_url or ""
    if started.expires_at:
        tx.expires_at = started.expires_at
    tx.request = (started.raw or {}).get("request") or started.raw or {}
    tx.response = (started.raw or {}).get("response") or {}
    tx.save()
    if send_to and tx.pay_url:
        send_pay_link(tx, send_to, by=by)
    return tx


# ── results ────────────────────────────────────────────────────────────────


def apply_result(tx: TerminalTransaction, result: Result, *, by=None, source: str = "") -> TerminalTransaction:
    """Write a provider / bridge / cashier result; approved → ledger payment (idempotent)."""
    with transaction.atomic():
        tx = (
            TerminalTransaction.objects.select_for_update(of=("self",))
            .select_related("terminal", "restaurant")
            .get(pk=tx.pk)
        )
        if tx.status in TerminalTransaction.FINAL:
            return tx
        if not result.final:
            if result.status and result.status != tx.status and tx.status in ("pending", "sent"):
                tx.status = result.status
            tx.response = result.raw or tx.response
            tx.save(update_fields=["status", "response", "updated_at"])
            return tx
        tx.status = result.status
        tx.external_id = result.external_id or tx.external_id
        tx.auth_code = result.auth_code or tx.auth_code
        tx.card_mask = result.card_mask or tx.card_mask
        tx.rrn = result.rrn or tx.rrn
        tx.error = (result.error or "")[:300]
        tx.response = result.raw or tx.response
        tx.finished_at = timezone.now()
        if by is not None and getattr(by, "is_authenticated", False):
            tx.confirmed_by = by
        if tx.status == "approved" and tx.kind == "sale":
            from apps.orders.models import Order

            kwargs = {}
            if tx.session_id:
                kwargs["session"] = tx.session
            elif tx.order_ids:
                kwargs["orders"] = list(Order.objects.filter(pk__in=tx.order_ids))
            else:
                kwargs["order"] = tx.order
            try:
                payment = ledger.record_payment(
                    tx.restaurant,
                    method=ledger_method(tx.terminal),
                    amount=tx.amount,
                    tip=tx.tip,
                    by=by,
                    external_id=f"tx:{tx.pk}",
                    allow_overpay=True,
                    notes=f"{tx.terminal.name}{' · ' + tx.card_mask if tx.card_mask else ''}",
                    **kwargs,
                )
            except ledger.LedgerError as exc:
                logger.error("terminal %s approved but ledger refused: %s", tx.pk, exc)
                tx.error = f"Approved at the terminal but not booked: {exc}"[:300]
                tx.status = "failed"
                tx.save()
                return tx
            tx.payment = payment
            Payment.objects.filter(pk=payment.pk).update(terminal=tx.terminal)
        tx.save()
    if tx.status in ("declined", "failed", "timeout"):
        try:
            from apps.notifications import hooks as notification_hooks

            notification_hooks.on_terminal_declined(tx)
        except Exception:  # pragma: no cover
            logger.exception("terminal notification failed")
    _audit(tx, by, source)
    return tx


def confirm_manual(tx: TerminalTransaction, *, by, auth_code: str = "", card_mask: str = "") -> TerminalTransaction:
    if tx.terminal.provider not in ("manual",):
        raise TerminalServiceError("not_manual", "Only manual terminals are confirmed by the cashier.")
    if tx.status not in TerminalTransaction.OPEN:
        raise TerminalServiceError("final", "This transaction is already finished.")
    return apply_result(
        tx, Result(status="approved", auth_code=auth_code[:40], card_mask=card_mask[:30]), by=by, source="cashier"
    )


def decline_manual(tx: TerminalTransaction, *, by, reason: str = "") -> TerminalTransaction:
    if tx.status not in TerminalTransaction.OPEN:
        raise TerminalServiceError("final", "This transaction is already finished.")
    return apply_result(
        tx, Result(status="declined", error=reason or "Declined by the terminal"), by=by, source="cashier"
    )


def cancel(tx: TerminalTransaction, *, by=None, session_http=None) -> TerminalTransaction:
    if tx.status not in TerminalTransaction.OPEN:
        return tx
    try:
        registry.get_provider(tx.terminal, session=session_http).cancel(tx)
    except TerminalError as exc:
        logger.info("terminal cancel at provider failed for %s: %s", tx.pk, exc)
    return apply_result(tx, Result(status="cancelled"), by=by, source="cancel")


def poll(tx: TerminalTransaction, *, session_http=None) -> TerminalTransaction:
    if tx.status not in TerminalTransaction.OPEN:
        return tx
    provider = registry.get_provider(tx.terminal, session=session_http)
    if not provider.polls:
        return tx
    try:
        result = provider.poll(tx)
    except TerminalError as exc:
        logger.info("terminal poll failed for %s: %s", tx.pk, exc)
        return tx
    if result is None:
        return tx
    return apply_result(tx, result, source="poll")


def expire_stale() -> int:
    n = 0
    now = timezone.now()
    for tx in TerminalTransaction.objects.filter(
        status__in=TerminalTransaction.OPEN, expires_at__lt=now
    ).select_related("terminal", "restaurant"):
        # Link providers may have been paid at the last second: ask once more before giving up.
        if tx.terminal.provider in PaymentTerminal.LINK_PROVIDERS and tx.external_id:
            tx = poll(tx)
            if tx.status in TerminalTransaction.FINAL:
                n += 1
                continue
        apply_result(tx, Result(status="timeout", error="No answer from the terminal in time."), source="expire")
        n += 1
    return n


def poll_open() -> int:
    n = 0
    for tx in (
        TerminalTransaction.objects.filter(
            status__in=("sent", "awaiting_confirm"), terminal__provider__in=PaymentTerminal.LINK_PROVIDERS
        )
        .exclude(external_id="")
        .select_related("terminal", "restaurant")
    ):
        before = tx.status
        tx = poll(tx)
        if tx.status != before:
            n += 1
    return n


# ── refunds ────────────────────────────────────────────────────────────────


def start_refund(payment: Payment, amount, *, by, reason: str = "", session_http=None):
    """Refund a terminal-taken payment: provider refund (link) / bridge job (ECR) / cashier (manual), then the ledger."""
    amount = q(amount)
    sale = TerminalTransaction.objects.filter(payment=payment, kind="sale", status="approved").first()
    if sale is None:
        raise TerminalServiceError("not_terminal", "This payment was not taken through a terminal.")
    tx = TerminalTransaction.objects.create(
        restaurant=payment.restaurant,
        terminal=sale.terminal,
        kind="refund",
        amount=amount,
        currency=sale.currency,
        status="pending",
        order=sale.order,
        session=sale.session,
        order_ids=sale.order_ids,
        payment=payment,
        refund_of=sale,
        external_id=sale.external_id,
        initiated_by=by if getattr(by, "is_authenticated", False) else None,
        expires_at=timezone.now() + timedelta(seconds=int(sale.terminal.timeout_seconds or 180)),
    )
    provider = registry.get_provider(sale.terminal, session=session_http)
    try:
        result = provider.refund(tx, amount)
    except TerminalError as exc:
        tx.status = "failed"
        tx.error = str(exc)[:300]
        tx.finished_at = timezone.now()
        tx.save()
        raise TerminalServiceError("provider_failed", str(exc)) from exc
    if result.status == "approved":
        refund = ledger.refund_payment(
            payment,
            amount=amount,
            by=by,
            method=("online" if sale.terminal.is_link else "card_terminal"),
            reason_details=reason,
        )
        tx.status = "approved"
        tx.finished_at = timezone.now()
        tx.response = {"refund_id": str(refund.pk)}
        tx.save()
        return tx, refund
    tx.status = result.status  # sent: the bridge will report back
    tx.save(update_fields=["status", "updated_at"])
    return tx, None


def complete_bridge_refund(tx: TerminalTransaction, result: Result, *, by=None):
    """Bridge reported the ECR refund: book it in the ledger when approved."""
    with transaction.atomic():
        tx = TerminalTransaction.objects.select_for_update(of=("self",)).select_related("terminal").get(pk=tx.pk)
        if tx.status in TerminalTransaction.FINAL:
            return tx
        tx.status = result.status if result.final else tx.status
        tx.auth_code = result.auth_code or tx.auth_code
        tx.rrn = result.rrn or tx.rrn
        tx.error = (result.error or "")[:300]
        tx.response = result.raw or tx.response
        if result.final:
            tx.finished_at = timezone.now()
        if tx.status == "approved" and tx.payment_id:
            refund = ledger.refund_payment(tx.payment, amount=tx.amount, by=by, method="card_terminal")
            tx.response = {**(tx.response or {}), "refund_id": str(refund.pk)}
        tx.save()
    return tx


# ── bridge ─────────────────────────────────────────────────────────────────


def heartbeat(terminal: PaymentTerminal) -> None:
    PaymentTerminal.objects.filter(pk=terminal.pk).update(last_seen_at=timezone.now())


def claim_next(terminal: PaymentTerminal) -> TerminalTransaction | None:
    heartbeat(terminal)
    with transaction.atomic():
        tx = (
            TerminalTransaction.objects.select_for_update(skip_locked=True)
            .filter(terminal=terminal, status="pending")
            .order_by("created_at")
            .first()
        )
        if tx is None:
            return None
        tx.status = "sent"
        tx.claimed_at = timezone.now()
        tx.save(update_fields=["status", "claimed_at", "updated_at"])
        return tx


def bridge_job(tx: TerminalTransaction) -> dict:
    return {
        "id": str(tx.pk),
        "kind": tx.kind,
        "amount": str(tx.total if tx.kind == "sale" else tx.amount),
        "currency": tx.currency,
        "protocol": tx.terminal.ecr_protocol,
        "device": tx.terminal.connection or {},
        "reference": tx.target_label,
        "rrn": tx.rrn if tx.kind == "refund" else "",
    }


# ── links ──────────────────────────────────────────────────────────────────


def send_pay_link(tx: TerminalTransaction, to: str, *, by=None):
    from apps.notifications import services as notifications

    r = tx.restaurant
    cfg = notifications.settings_for(r)
    channel = "email" if "@" in to else "sms"
    lang = getattr(r, "default_language", "ka") or "ka"
    text = notifications.render(cfg.template("pay_link", lang), restaurant=r.name, amount=str(tx.total), url=tx.pay_url)
    tx.sent_to = to[:254]
    tx.save(update_fields=["sent_to", "updated_at"])
    return notifications.send_message(r, channel, to, text, subject=r.name, kind="pay_link", ref=tx, by=by)


# ── reconciliation ─────────────────────────────────────────────────────────


def reconcile(restaurant, day) -> dict:
    """Card payments in the ledger vs approved terminal transactions for one local day."""
    from django.db.models import Count, Sum

    from apps.tenants import hours as H

    zone = H.tz(restaurant)
    from datetime import datetime

    start = datetime.combine(day, datetime.min.time(), tzinfo=zone)
    end = start + timedelta(days=1)
    approved = (
        TerminalTransaction.objects.filter(
            restaurant=restaurant, kind="sale", status="approved", finished_at__gte=start, finished_at__lt=end
        )
        .values("terminal__id", "terminal__name")
        .annotate(n=Count("id"), amount=Sum("amount"), tips=Sum("tip"))
        .order_by("terminal__name")
    )
    booked = {
        r["terminal_id"]: r
        for r in Payment.objects.filter(
            restaurant=restaurant,
            status__in=("completed", "partially_refunded", "refunded"),
            completed_at__gte=start,
            completed_at__lt=end,
            terminal__isnull=False,
        )
        .values("terminal_id")
        .annotate(n=Count("id"), amount=Sum("amount"))
    }
    untracked = Payment.objects.filter(
        restaurant=restaurant,
        status__in=("completed", "partially_refunded", "refunded"),
        completed_at__gte=start,
        completed_at__lt=end,
        payment_method="card_terminal",
        terminal__isnull=True,
    ).aggregate(n=Count("id"), amount=Sum("amount"))
    rows = []
    for r in approved:
        b = booked.get(r["terminal__id"], {})
        rows.append(
            {
                "terminal": r["terminal__name"],
                "approved_count": r["n"],
                "approved_amount": q(r["amount"]),
                "tips": q(r["tips"]),
                "booked_count": b.get("n", 0),
                "booked_amount": q(b.get("amount")),
                "difference": q(r["amount"]) - q(b.get("amount")),
            }
        )
    declined = TerminalTransaction.objects.filter(
        restaurant=restaurant,
        kind="sale",
        status__in=("declined", "timeout", "failed"),
        created_at__gte=start,
        created_at__lt=end,
    ).count()
    return {
        "day": day,
        "rows": rows,
        "declined": declined,
        "untracked_count": untracked["n"] or 0,
        "untracked_amount": q(untracked["amount"]),
    }


def summary(restaurant) -> dict:
    today = timezone.localdate()
    open_tx = TerminalTransaction.objects.filter(restaurant=restaurant, status__in=TerminalTransaction.OPEN)
    return {
        "awaiting": open_tx.count(),
        "declined_today": TerminalTransaction.objects.filter(
            restaurant=restaurant, status__in=("declined", "timeout", "failed"), created_at__date=today
        ).count(),
        "offline_bridges": sum(1 for t in terminals_for(restaurant).filter(provider="ecr_bridge") if not t.is_online),
        "terminals": terminals_for(restaurant).count(),
    }


def _audit(tx, by, source):
    try:
        from apps.audit.services import log_action

        if tx.status not in ("approved", "declined", "cancelled", "timeout", "failed"):
            return
        log_action(
            "terminal_refund" if tx.kind == "refund" else "terminal_sale",
            user=by if getattr(by, "is_authenticated", False) else None,
            restaurant=tx.restaurant,
            description=f"{tx.terminal.name}: {tx.get_kind_display()} {tx.total} {tx.currency} {tx.get_status_display()} ({source})",
            target_model="TerminalTransaction",
            target_id=str(tx.pk),
            changes={"status": tx.status, "amount": str(tx.amount), "card": tx.card_mask},
        )
    except Exception:  # pragma: no cover
        logger.exception("terminal audit failed")
