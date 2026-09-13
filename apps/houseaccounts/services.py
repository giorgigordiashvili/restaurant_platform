"""Charge to an account, settle it, statements, overdue list, report."""

from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.houseaccounts.models import HouseAccount, HouseAccountEntry, HouseAccountStatement

logger = logging.getLogger(__name__)
ZERO = Decimal("0.00")


class HouseAccountError(Exception):
    def __init__(self, code: str, message: str = "", **extra):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.extra = extra


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "house_accounts_enabled", False))


def q(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def statement_url(statement: HouseAccountStatement) -> str:
    base = getattr(settings, "FRONTEND_BASE_URL", "https://aimenu.ge").rstrip("/")
    return f"{base}/ha/{statement.token}"


def _entry(account, kind, amount, *, order=None, payment=None, by=None, signed_by="", note="") -> HouseAccountEntry:
    return HouseAccountEntry.objects.create(
        account=account,
        kind=kind,
        amount=q(amount),
        balance_after=account.balance,
        order=order,
        payment=payment,
        by=by if getattr(by, "is_authenticated", False) else None,
        signed_by=(signed_by or "")[:120],
        note=(note or "")[:200],
    )


def lookup(restaurant, *, phone: str = "", q_text: str = ""):
    qs = HouseAccount.objects.filter(restaurant=restaurant, status="active")
    if phone:
        from apps.notifications.providers import normalize_phone

        return qs.filter(phone=normalize_phone(phone)).first()
    if q_text:
        from django.db.models import Q

        return list(
            qs.filter(Q(name__icontains=q_text) | Q(company__icontains=q_text) | Q(phone__icontains=q_text))[:20]
        )
    return list(qs[:50])


def charge(account: HouseAccount, amount, *, by, order=None, session=None, orders=None, signed_by="", note=""):
    """Put (part of) a bill on the account: a ``house_account`` ledger payment + a charge entry."""
    from apps.payments import services as ledger

    amount = q(amount)
    if amount <= ZERO:
        raise HouseAccountError("invalid_amount", "Amount must be greater than zero.")
    with transaction.atomic():
        account = HouseAccount.objects.select_for_update().get(pk=account.pk)
        if account.status != "active":
            raise HouseAccountError("not_active", "This account is suspended or closed.")
        if account.credit_limit and account.balance + amount > account.credit_limit:
            raise HouseAccountError(
                "credit_limit",
                f"Only {max(account.credit_limit - account.balance, ZERO)} of credit left on this account.",
                available=str(max(account.credit_limit - account.balance, ZERO)),
            )
        if account.require_signature and not signed_by:
            raise HouseAccountError("signature_required", "Enter who signed for this charge.")
        payment = ledger.record_payment(
            account.restaurant,
            method="house_account",
            amount=amount,
            by=by,
            order=order,
            session=session,
            orders=orders,
            house_account=account,
            external_id=f"ha:{account.pk}:{timezone.now().timestamp()}",
            notes=f"House account {account.name}{' · ' + signed_by if signed_by else ''}",
        )
        account.balance = q(account.balance + amount)
        account.save(update_fields=["balance", "updated_at"])
        entry = _entry(
            account,
            "charge",
            amount,
            order=order or payment.order,
            payment=payment,
            by=by,
            signed_by=signed_by,
            note=note,
        )
    _audit(account, by, "house_account_charge", f"{account.name} +{amount} (balance {account.balance})")
    if account.credit_limit and account.balance >= account.credit_limit * Decimal("0.9"):
        try:
            from apps.notifications import hooks as notification_hooks

            notification_hooks.on_house_account_limit(account)
        except Exception:  # pragma: no cover
            logger.exception("house account limit alert failed")
    return payment, entry


def settle(account: HouseAccount, amount, *, method: str, by, tendered=None, note=""):
    """Money received against the account (cash / card at the till): a real ledger payment, no order."""
    from apps.payments import services as ledger

    amount = q(amount)
    if amount <= ZERO:
        raise HouseAccountError("invalid_amount", "Amount must be greater than zero.")
    if method not in ("cash", "card_terminal", "other", "online_bog", "online_tbc"):
        raise HouseAccountError("invalid_method", "Settle with cash, card or other.")
    with transaction.atomic():
        account = HouseAccount.objects.select_for_update().get(pk=account.pk)
        payment = ledger.record_payment(
            account.restaurant,
            method=method,
            amount=amount,
            by=by,
            tendered=tendered,
            house_account=account,
            notes=f"House account settlement {account.name}",
        )
        account.balance = q(account.balance - amount)
        account.last_payment_at = timezone.now()
        account.save(update_fields=["balance", "last_payment_at", "updated_at"])
        entry = _entry(account, "payment", -amount, payment=payment, by=by, note=note)
    _audit(account, by, "house_account_settle", f"{account.name} -{amount} {method} (balance {account.balance})")
    return payment, entry


def adjust(account: HouseAccount, amount, *, by, note: str, writeoff: bool = False) -> HouseAccountEntry:
    amount = q(amount)
    with transaction.atomic():
        account = HouseAccount.objects.select_for_update().get(pk=account.pk)
        account.balance = q(account.balance + amount)
        account.save(update_fields=["balance", "updated_at"])
        return _entry(account, "writeoff" if writeoff else "adjustment", amount, by=by, note=note)


def set_status(account: HouseAccount, status: str, *, by=None) -> HouseAccount:
    if status == "closed" and account.balance != ZERO:
        raise HouseAccountError("balance_open", "Settle or write off the balance before closing.")
    account.status = status
    account.save(update_fields=["status", "updated_at"])
    return account


# ── statements ─────────────────────────────────────────────────────────────


def build_statement(account: HouseAccount, start: date, end: date, *, save: bool = True) -> HouseAccountStatement:
    entries = HouseAccountEntry.objects.filter(
        account=account, created_at__date__gte=start, created_at__date__lte=end
    ).order_by("created_at")
    before = (
        HouseAccountEntry.objects.filter(account=account, created_at__date__lt=start).aggregate(s=Sum("amount"))["s"]
        or ZERO
    )
    charges = sum((e.amount for e in entries if e.kind == "charge"), ZERO)
    payments = sum((-e.amount for e in entries if e.kind == "payment"), ZERO)
    adjustments = sum((e.amount for e in entries if e.kind in ("adjustment", "writeoff")), ZERO)
    lines = [
        {
            "date": e.created_at.date().isoformat(),
            "kind": e.kind,
            "label": e.get_kind_display(),
            "order": e.order.order_number if e.order_id else "",
            "signed_by": e.signed_by,
            "note": e.note,
            "amount": str(e.amount),
            "balance_after": str(e.balance_after),
        }
        for e in entries
    ]
    data = dict(
        account=account,
        period_start=start,
        period_end=end,
        opening=q(before),
        charges=q(charges),
        payments=q(payments),
        adjustments=q(adjustments),
        closing=q(before + charges - payments + adjustments),
        lines=lines,
    )
    if not save:
        return HouseAccountStatement(**data)
    stmt, _created = HouseAccountStatement.objects.update_or_create(
        account=account,
        period_start=start,
        period_end=end,
        defaults={k: v for k, v in data.items() if k not in ("account", "period_start", "period_end")},
    )
    return stmt


def previous_month(today: date | None = None) -> tuple[date, date]:
    today = today or timezone.localdate()
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    return last_prev.replace(day=1), last_prev


def send_statement(stmt: HouseAccountStatement, *, by=None, force: bool = False) -> bool:
    try:
        from apps.notifications import services as notifications

        account = stmt.account
        r = account.restaurant
        if not notifications.enabled(r):
            return False
        cfg = notifications.settings_for(r)
        lang = getattr(r, "default_language", "ka") or "ka"
        text = notifications.render(
            cfg.template("statement", lang),
            restaurant=r.name,
            name=account.name,
            amount=str(stmt.closing),
            period=f"{stmt.period_start:%d.%m} – {stmt.period_end:%d.%m.%Y}",
            url=statement_url(stmt),
        )
        sent_to = ""
        if account.email:
            notifications.send_message(
                r,
                "email",
                account.email,
                text,
                subject=f"{r.name} · statement",
                kind="statement",
                ref=stmt,
                by=by,
                force=force,
            )
            sent_to = account.email
        elif account.phone:
            notifications.send_message(r, "sms", account.phone, text, kind="statement", ref=stmt, by=by, force=force)
            sent_to = account.phone
        if sent_to:
            stmt.sent_at = timezone.now()
            stmt.sent_to = sent_to
            stmt.save(update_fields=["sent_at", "sent_to", "updated_at"])
        return bool(sent_to)
    except Exception:  # noqa: BLE001
        logger.exception("statement send failed for %s", stmt.pk)
        return False


def monthly_statements(today: date | None = None) -> int:
    """Beat (daily): on each account's billing day, build last month's statement and send it."""
    today = today or timezone.localdate()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    n = 0
    for account in HouseAccount.objects.filter(status="active").select_related("restaurant"):
        if not enabled(account.restaurant):
            continue
        billing_day = min(int(account.billing_day or 1), days_in_month)
        if today.day != billing_day:
            continue
        start, end = previous_month(today)
        if HouseAccountStatement.objects.filter(
            account=account, period_start=start, period_end=end, sent_at__isnull=False
        ).exists():
            continue
        if (
            not HouseAccountEntry.objects.filter(
                account=account, created_at__date__gte=start, created_at__date__lte=end
            ).exists()
            and account.balance == ZERO
        ):
            continue
        stmt = build_statement(account, start, end)
        send_statement(stmt)
        n += 1
    return n


# ── reporting ──────────────────────────────────────────────────────────────


def overdue(restaurant, days: int = 30):
    cutoff = timezone.now() - timedelta(days=days)
    return [
        a
        for a in HouseAccount.objects.filter(restaurant=restaurant, status="active", balance__gt=0)
        if (a.last_payment_at or a.created_at) < cutoff
    ]


def report(restaurant, start, end) -> dict:
    charged = HouseAccountEntry.objects.filter(
        account__restaurant=restaurant, kind="charge", created_at__date__gte=start, created_at__date__lte=end
    ).aggregate(v=Sum("amount"))["v"]
    settled = HouseAccountEntry.objects.filter(
        account__restaurant=restaurant, kind="payment", created_at__date__gte=start, created_at__date__lte=end
    ).aggregate(v=Sum("amount"))["v"]
    return {
        "charged": q(charged),
        "settled": q(-(settled or ZERO)),
        "outstanding": q(
            HouseAccount.objects.filter(restaurant=restaurant, status="active").aggregate(v=Sum("balance"))["v"]
        ),
        "overdue": len(overdue(restaurant)),
    }


def summary(restaurant) -> dict:
    return {
        "accounts": HouseAccount.objects.filter(restaurant=restaurant, status="active").count(),
        "outstanding": q(
            HouseAccount.objects.filter(restaurant=restaurant, status="active").aggregate(v=Sum("balance"))["v"]
        ),
        "overdue": len(overdue(restaurant)),
    }


def _audit(account, by, action, description) -> None:
    try:
        from apps.audit.services import log_action

        log_action(
            action,
            user=by if getattr(by, "is_authenticated", False) else None,
            restaurant=account.restaurant,
            description=description,
            target_model="HouseAccount",
            target_id=str(account.pk),
        )
    except Exception:  # pragma: no cover
        logger.exception("house account audit failed")
