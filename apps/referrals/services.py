"""
Service layer for the referral / wallet system.

All mutations are wrapped in transaction.atomic + select_for_update on the
referrer/spender's UserProfile row so concurrent webhook deliveries can't
race the cached `wallet_balance` out of sync with the ledger.

Idempotency: the paid-order hooks fire once per order in the happy path, but
BOG and Flitt both retry webhooks — every credit / debit is guarded by an
existence check on (user, source_order, kind) so re-delivery is a no-op.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum

from apps.accounts.models import User, UserProfile
from apps.orders.models import Order

from .models import WalletTransaction

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")


class InsufficientBalance(Exception):
    """Raised when spend_wallet is asked to debit more than the user holds."""


def _quantize(amount: Decimal) -> Decimal:
    return amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def effective_percent(referrer: User) -> Decimal:
    """Resolve the commission percent for a referrer — override or default."""
    profile = getattr(referrer, "profile", None)
    if profile and profile.referral_percent_override is not None:
        return Decimal(profile.referral_percent_override)
    return Decimal(str(settings.REFERRAL_DEFAULT_PERCENT))


def _credit(
    *,
    user: User,
    kind: str,
    amount: Decimal,
    source_order: Order | None,
    referred_user: User | None = None,
    created_by: User | None = None,
    notes: str = "",
) -> WalletTransaction:
    """
    Internal helper — caller is responsible for atomic + select_for_update on the
    target profile. ``amount`` is signed (positive credit, negative debit).
    """
    profile = UserProfile.objects.select_for_update().get(user=user)
    new_balance = (Decimal(profile.wallet_balance) + amount).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    if new_balance < 0:
        raise InsufficientBalance(f"Wallet for user {user.pk} would go negative: {profile.wallet_balance} + {amount}")
    profile.wallet_balance = new_balance
    profile.save(update_fields=["wallet_balance", "updated_at"])
    return WalletTransaction.objects.create(
        user=user,
        kind=kind,
        amount=amount,
        balance_after=new_balance,
        source_order=source_order,
        referred_user=referred_user,
        created_by=created_by,
        notes=notes,
    )


def credit_referral(order: Order) -> WalletTransaction | None:
    """
    Credit the referrer's wallet for a paid order placed by a referred user.

    Returns the created WalletTransaction, or None when there's nothing to do
    (no referrer, zero-amount calculation, or already credited for this order).
    """
    if order is None or order.customer_id is None:
        return None
    profile = getattr(order.customer, "profile", None)
    if profile is None or profile.referred_by_id is None:
        return None
    referrer_id = profile.referred_by_id

    # Compute on gross — order.subtotal + tax + service − discount − wallet,
    # i.e. the amount the customer actually settled. Using order.total mirrors
    # what we'd report as gross for the platform commission split.
    gross = Decimal(order.total or 0)
    if gross <= 0:
        return None

    with transaction.atomic():
        referrer = User.objects.select_related("profile").get(pk=referrer_id)
        if WalletTransaction.objects.filter(
            user=referrer,
            source_order=order,
            kind=WalletTransaction.KIND_REFERRAL_CREDIT,
        ).exists():
            return None
        percent = effective_percent(referrer)
        amount = _quantize(gross * percent / Decimal(100))
        if amount <= 0:
            return None
        return _credit(
            user=referrer,
            kind=WalletTransaction.KIND_REFERRAL_CREDIT,
            amount=amount,
            source_order=order,
            referred_user=order.customer,
            notes=f"{percent}% of order {order.order_number}",
        )


def clawback_referral(order: Order) -> WalletTransaction | None:
    """Reverse the referral credit for an order that was refunded."""
    if order is None or order.customer_id is None:
        return None
    profile = getattr(order.customer, "profile", None)
    if profile is None or profile.referred_by_id is None:
        return None
    referrer_id = profile.referred_by_id

    with transaction.atomic():
        original = WalletTransaction.objects.filter(
            user_id=referrer_id,
            source_order=order,
            kind=WalletTransaction.KIND_REFERRAL_CREDIT,
        ).first()
        if original is None:
            return None
        if WalletTransaction.objects.filter(
            user_id=referrer_id,
            source_order=order,
            kind=WalletTransaction.KIND_REFERRAL_CLAWBACK,
        ).exists():
            return None
        referrer = User.objects.select_related("profile").get(pk=referrer_id)
        return _credit(
            user=referrer,
            kind=WalletTransaction.KIND_REFERRAL_CLAWBACK,
            amount=-original.amount,
            source_order=order,
            referred_user=order.customer,
            notes=f"Clawback for refunded order {order.order_number}",
        )


def spend_wallet(user: User, amount: Decimal, order: Order) -> WalletTransaction | None:
    """
    Debit ``amount`` from the user's wallet at payment success.

    Idempotent per order: a second call for the same order returns the existing
    debit row without writing again. Raises InsufficientBalance if the cached
    balance is below ``amount`` (race with an earlier debit).
    """
    if amount is None or amount <= 0 or order is None or user is None:
        return None
    debit = -_quantize(Decimal(amount))

    with transaction.atomic():
        existing = WalletTransaction.objects.filter(
            user=user,
            source_order=order,
            kind=WalletTransaction.KIND_ORDER_SPEND,
        ).first()
        if existing:
            return existing
        return _credit(
            user=user,
            kind=WalletTransaction.KIND_ORDER_SPEND,
            amount=debit,
            source_order=order,
            notes=f"Wallet applied to order {order.order_number}",
        )


def refund_wallet_spend(order: Order) -> WalletTransaction | None:
    """Reverse a wallet debit when the underlying order is refunded."""
    if order is None or order.customer_id is None:
        return None

    with transaction.atomic():
        original = WalletTransaction.objects.filter(
            user_id=order.customer_id,
            source_order=order,
            kind=WalletTransaction.KIND_ORDER_SPEND,
        ).first()
        if original is None:
            return None
        if WalletTransaction.objects.filter(
            user_id=order.customer_id,
            source_order=order,
            kind=WalletTransaction.KIND_REFUND_CREDIT,
        ).exists():
            return None
        return _credit(
            user=order.customer,
            kind=WalletTransaction.KIND_REFUND_CREDIT,
            amount=-original.amount,
            source_order=order,
            notes=f"Wallet refunded for order {order.order_number}",
        )


def manual_adjustment(*, user: User, amount: Decimal, created_by: User, notes: str = "") -> WalletTransaction:
    """Admin-driven credit / debit. Sign-aware ``amount``."""
    with transaction.atomic():
        return _credit(
            user=user,
            kind=WalletTransaction.KIND_MANUAL_ADJUSTMENT,
            amount=_quantize(Decimal(amount)),
            source_order=None,
            created_by=created_by,
            notes=notes,
        )


def recalculate_balance(user: User) -> Decimal:
    """Sum the ledger and overwrite the cached profile balance. Admin tool."""
    total = WalletTransaction.objects.filter(user=user).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    total = _quantize(total)
    UserProfile.objects.filter(user=user).update(wallet_balance=total)
    return total
