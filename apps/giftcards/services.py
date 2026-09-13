"""Issue, sell, look up, redeem, refund-to-card, void, deliver, expire, report."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.giftcards.models import GiftCard, GiftCardTransaction

logger = logging.getLogger(__name__)
ZERO = Decimal("0.00")


class GiftCardError(Exception):
    def __init__(self, code: str, message: str = "", **extra):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.extra = extra


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "gift_cards_enabled", False))


def q(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def default_expiry(restaurant):
    months = int(getattr(settings, "GIFT_CARD_DEFAULT_MONTHS", 12) or 0)
    return timezone.now() + timedelta(days=30 * months) if months else None


def card_url(card: GiftCard) -> str:
    base = getattr(settings, "FRONTEND_BASE_URL", "https://aimenu.ge").rstrip("/")
    return f"{base}/gc/{card.token}"


def normalize_code(raw: str) -> str:
    s = "".join(ch for ch in (raw or "").upper() if ch.isalnum())
    if s.startswith("GC"):
        s = s[2:]
    return f"GC-{s[:4]}-{s[4:8]}" if len(s) >= 8 else raw.strip().upper()


# ── lifecycle ──────────────────────────────────────────────────────────────


def _log(card: GiftCard, kind: str, amount, *, payment=None, order=None, by=None, note="") -> GiftCardTransaction:
    return GiftCardTransaction.objects.create(
        card=card,
        kind=kind,
        amount=q(amount),
        balance_after=card.balance,
        payment=payment,
        order=order,
        by=by if getattr(by, "is_authenticated", False) else None,
        note=note[:200],
    )


def issue(
    restaurant,
    amount,
    *,
    kind: str = "physical",
    by=None,
    status: str = "active",
    payment=None,
    purchaser: dict | None = None,
    recipient: dict | None = None,
    message: str = "",
    design: str = "classic",
    sold_online: bool = False,
    expires_at=None,
    notes: str = "",
) -> GiftCard:
    amount = q(amount)
    if amount <= ZERO:
        raise GiftCardError("invalid_amount", "Amount must be greater than zero.")
    purchaser = purchaser or {}
    recipient = recipient or {}
    with transaction.atomic():
        card = GiftCard.objects.create(
            restaurant=restaurant,
            initial_value=amount,
            balance=amount if status == "active" else ZERO,
            currency=getattr(restaurant, "default_currency", "GEL") or "GEL",
            status=status,
            kind=kind,
            design=design if design in dict(GiftCard.DESIGN_CHOICES) else "classic",
            expires_at=expires_at if expires_at is not None else default_expiry(restaurant),
            purchaser_name=(purchaser.get("name") or "")[:120],
            purchaser_phone=(purchaser.get("phone") or "")[:20],
            purchaser_email=purchaser.get("email") or "",
            recipient_name=(recipient.get("name") or "")[:120],
            recipient_phone=(recipient.get("phone") or "")[:20],
            recipient_email=recipient.get("email") or "",
            message=(message or "")[:300],
            sold_by=by if getattr(by, "is_authenticated", False) else None,
            sold_payment=payment,
            sold_online=sold_online,
            notes=(notes or "")[:200],
        )
        if status == "active":
            _log(card, "issue", amount, payment=payment, by=by, note="Issued")
    _audit(card, by, "gift_card_issued", f"{card.code} {amount} {card.currency}")
    return card


def activate(card: GiftCard, *, payment=None, by=None) -> GiftCard:
    """A pending (online) card becomes active once the money arrived."""
    with transaction.atomic():
        card = GiftCard.objects.select_for_update().get(pk=card.pk)
        if card.status != "pending":
            return card
        card.status = "active"
        card.balance = card.initial_value
        card.sold_payment = payment or card.sold_payment
        card.save(update_fields=["status", "balance", "sold_payment", "updated_at"])
        _log(card, "issue", card.initial_value, payment=payment, by=by, note="Paid online")
    _audit(card, by, "gift_card_issued", f"{card.code} {card.initial_value} {card.currency} (online)")
    deliver_digital(card)
    return card


def sell_pos(
    restaurant, amount, *, method: str, by, tendered=None, tip=ZERO, kind="physical", **card_fields
) -> GiftCard:
    """Sell a card at the till: the money is a ledger payment (no order), then the card is issued."""
    from apps.payments import services as ledger

    amount = q(amount)
    with transaction.atomic():
        card = issue(restaurant, amount, kind=kind, by=by, **card_fields)
        payment = ledger.record_payment(
            restaurant,
            method=method,
            amount=amount,
            by=by,
            tendered=tendered,
            gift_card=card,
            notes=f"Gift card {card.code}",
        )
        card.sold_payment = payment
        card.save(update_fields=["sold_payment", "updated_at"])
        GiftCardTransaction.objects.filter(card=card, kind="issue").update(payment=payment)
    if card.kind == "digital":
        deliver_digital(card)
    return card


def lookup(restaurant, code: str) -> GiftCard:
    card = GiftCard.objects.filter(restaurant=restaurant, code=normalize_code(code)).first()
    if card is None:
        raise GiftCardError("not_found", "No gift card with this code.")
    return card


def check_usable(card: GiftCard) -> None:
    if card.status == "pending":
        raise GiftCardError("not_paid", "This gift card has not been paid for yet.")
    if card.status == "void":
        raise GiftCardError("void", "This gift card was voided.")
    if card.expires_at and card.expires_at < timezone.now():
        raise GiftCardError("expired", "This gift card has expired.")
    if card.balance <= ZERO or card.status == "used_up":
        raise GiftCardError("used_up", "This gift card has no balance left.")


def redeem(card: GiftCard, amount, *, order=None, session=None, orders=None, by=None, allow_overpay=False):
    """Pay (part of) a bill with the card: a ``gift_card`` ledger payment + a redeem transaction."""
    from apps.payments import services as ledger

    amount = q(amount)
    with transaction.atomic():
        card = GiftCard.objects.select_for_update().get(pk=card.pk)
        check_usable(card)
        if amount <= ZERO:
            raise GiftCardError("invalid_amount", "Amount must be greater than zero.")
        if amount > card.balance:
            raise GiftCardError(
                "insufficient_balance", f"Only {card.balance} left on this card.", balance=str(card.balance)
            )
        payment = ledger.record_payment(
            card.restaurant,
            method="gift_card",
            amount=amount,
            by=by,
            order=order,
            session=session,
            orders=orders,
            gift_card=card,
            allow_overpay=allow_overpay,
            external_id=f"gc:{card.pk}:{timezone.now().timestamp()}",
            notes=f"Gift card {card.masked_code}",
        )
        card.balance = q(card.balance - amount)
        if card.balance <= ZERO:
            card.status = "used_up"
        card.save(update_fields=["balance", "status", "updated_at"])
        _log(card, "redeem", -amount, payment=payment, order=order or payment.order, by=by)
    _audit(card, by, "gift_card_redeemed", f"{card.code} -{amount} (balance {card.balance})")
    return payment


def refund_to_card(card: GiftCard, amount, *, by=None, order=None, note="") -> GiftCard:
    amount = q(amount)
    if amount <= ZERO:
        raise GiftCardError("invalid_amount", "Amount must be greater than zero.")
    with transaction.atomic():
        card = GiftCard.objects.select_for_update().get(pk=card.pk)
        if card.status == "void":
            raise GiftCardError("void", "This gift card was voided.")
        card.balance = q(card.balance + amount)
        if card.status in ("used_up", "expired"):
            card.status = "active"
        card.save(update_fields=["balance", "status", "updated_at"])
        _log(card, "refund", amount, order=order, by=by, note=note or "Refunded to card")
    return card


def adjust(card: GiftCard, amount, *, by, note: str) -> GiftCard:
    amount = q(amount)
    with transaction.atomic():
        card = GiftCard.objects.select_for_update().get(pk=card.pk)
        if card.balance + amount < ZERO:
            raise GiftCardError("insufficient_balance", "Balance cannot go below zero.")
        card.balance = q(card.balance + amount)
        card.status = (
            "used_up"
            if card.balance <= ZERO and card.status == "active"
            else ("active" if card.balance > ZERO and card.status == "used_up" else card.status)
        )
        card.save(update_fields=["balance", "status", "updated_at"])
        _log(card, "adjust", amount, by=by, note=note)
    return card


def void(card: GiftCard, *, by, note: str = "") -> GiftCard:
    with transaction.atomic():
        card = GiftCard.objects.select_for_update().get(pk=card.pk)
        forfeited = card.balance
        card.status = "void"
        card.balance = ZERO
        card.save(update_fields=["status", "balance", "updated_at"])
        _log(card, "void", -forfeited, by=by, note=note or "Voided")
    return card


def expire() -> int:
    now = timezone.now()
    qs = GiftCard.objects.filter(status="active", expires_at__lt=now)
    n = 0
    for card in qs:
        card.status = "expired"
        card.save(update_fields=["status", "updated_at"])
        _log(card, "adjust", ZERO, note="Expired")
        n += 1
    return n


# ── delivery ───────────────────────────────────────────────────────────────


def deliver_digital(card: GiftCard, *, by=None, force: bool = False) -> bool:
    """SMS / email the code to the recipient (or purchaser)."""
    try:
        from apps.notifications import services as notifications

        r = card.restaurant
        if not notifications.enabled(r):
            return False
        cfg = notifications.settings_for(r)
        lang = getattr(r, "default_language", "ka") or "ka"
        text = notifications.render(
            cfg.template("gift_card", lang),
            restaurant=r.name,
            amount=str(card.initial_value),
            code=card.code,
            url=card_url(card),
            name=card.recipient_name or card.purchaser_name,
            message=card.message,
        )
        phone = card.recipient_phone or card.purchaser_phone
        email = card.recipient_email or card.purchaser_email
        sent = False
        if phone:
            notifications.send_message(r, "sms", phone, text, kind="gift_card", ref=card, by=by, force=force)
            sent = True
        if email:
            notifications.send_message(
                r, "email", email, text, subject=f"{r.name} · gift card", kind="gift_card", ref=card, by=by, force=force
            )
            sent = True
        if sent:
            card.delivered_at = timezone.now()
            card.save(update_fields=["delivered_at", "updated_at"])
        return sent
    except Exception:  # noqa: BLE001
        logger.exception("gift card delivery failed for %s", card.pk)
        return False


# ── reporting ──────────────────────────────────────────────────────────────


def report(restaurant, start, end) -> dict:
    from django.db.models import Count, Sum

    sold = GiftCard.objects.filter(
        restaurant=restaurant,
        status__in=("active", "used_up", "expired"),
        created_at__date__gte=start,
        created_at__date__lte=end,
    ).aggregate(n=Count("id"), value=Sum("initial_value"))
    redeemed = GiftCardTransaction.objects.filter(
        card__restaurant=restaurant, kind="redeem", created_at__date__gte=start, created_at__date__lte=end
    ).aggregate(n=Count("id"), value=Sum("amount"))
    liability = GiftCard.objects.filter(restaurant=restaurant, status="active").aggregate(v=Sum("balance"))["v"]
    return {
        "sold_count": sold["n"] or 0,
        "sold_value": q(sold["value"]),
        "redeemed_count": redeemed["n"] or 0,
        "redeemed_value": q(-(redeemed["value"] or ZERO)),
        "outstanding": q(liability),
    }


def summary(restaurant) -> dict:
    from django.db.models import Sum

    today = timezone.localdate()
    return {
        "outstanding": q(
            GiftCard.objects.filter(restaurant=restaurant, status="active").aggregate(v=Sum("balance"))["v"]
        ),
        "active": GiftCard.objects.filter(restaurant=restaurant, status="active").count(),
        "sold_today": GiftCard.objects.filter(restaurant=restaurant, created_at__date=today)
        .exclude(status="pending")
        .count(),
        "redeemed_today": q(
            -(
                GiftCardTransaction.objects.filter(
                    card__restaurant=restaurant, kind="redeem", created_at__date=today
                ).aggregate(v=Sum("amount"))["v"]
                or ZERO
            )
        ),
    }


def _audit(card, by, action, description) -> None:
    try:
        from apps.audit.services import log_action

        log_action(
            action,
            user=by if getattr(by, "is_authenticated", False) else None,
            restaurant=card.restaurant,
            description=description,
            target_model="GiftCard",
            target_id=str(card.pk),
        )
    except Exception:  # pragma: no cover
        logger.exception("gift card audit failed")
