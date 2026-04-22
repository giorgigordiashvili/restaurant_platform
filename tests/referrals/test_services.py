"""Unit tests for apps.referrals.services."""

from decimal import Decimal

import pytest

from apps.referrals.models import WalletTransaction
from apps.referrals.services import (
    InsufficientBalance,
    clawback_referral,
    credit_referral,
    effective_percent,
    manual_adjustment,
    recalculate_balance,
    refund_wallet_spend,
    spend_wallet,
)


def _set_referrer(referee, referrer):
    referee.profile.referred_by = referrer
    referee.profile.save(update_fields=["referred_by", "updated_at"])


@pytest.fixture
def order_factory(db, create_order, restaurant):
    """Build an Order with controllable totals — calculate_totals isn't enough on its own."""
    from apps.orders.models import Order

    def _build(*, customer, total=Decimal("100.00")):
        order = create_order(
            restaurant=restaurant,
            customer=customer,
            order_type="dine_in",
            status="pending",
        )
        Order.objects.filter(pk=order.pk).update(total=total, subtotal=total)
        order.refresh_from_db()
        return order

    return _build


@pytest.mark.django_db
class TestEffectivePercent:
    def test_default_when_no_override(self, user, settings):
        settings.REFERRAL_DEFAULT_PERCENT = "0.5"
        assert effective_percent(user) == Decimal("0.5")

    def test_override_wins_when_set(self, user, settings):
        settings.REFERRAL_DEFAULT_PERCENT = "0.5"
        user.profile.referral_percent_override = Decimal("5.00")
        user.profile.save(update_fields=["referral_percent_override", "updated_at"])
        assert effective_percent(user) == Decimal("5.00")


@pytest.mark.django_db
class TestCreditReferral:
    def test_credits_default_percent(self, user, another_user, order_factory, settings):
        settings.REFERRAL_DEFAULT_PERCENT = "0.5"
        _set_referrer(referee=another_user, referrer=user)
        order = order_factory(customer=another_user, total=Decimal("100"))

        txn = credit_referral(order)

        assert txn is not None
        assert txn.amount == Decimal("0.50")
        assert txn.kind == WalletTransaction.KIND_REFERRAL_CREDIT
        user.profile.refresh_from_db()
        assert user.profile.wallet_balance == Decimal("0.50")

    def test_override_percent_used(self, user, another_user, order_factory):
        _set_referrer(referee=another_user, referrer=user)
        user.profile.referral_percent_override = Decimal("5.00")
        user.profile.save(update_fields=["referral_percent_override", "updated_at"])
        order = order_factory(customer=another_user, total=Decimal("100"))

        txn = credit_referral(order)
        assert txn.amount == Decimal("5.00")

    def test_no_referrer_is_noop(self, another_user, order_factory):
        # another_user has no referred_by set.
        order = order_factory(customer=another_user, total=Decimal("100"))
        assert credit_referral(order) is None

    def test_idempotent(self, user, another_user, order_factory):
        _set_referrer(referee=another_user, referrer=user)
        order = order_factory(customer=another_user, total=Decimal("100"))
        first = credit_referral(order)
        second = credit_referral(order)
        assert first is not None
        assert second is None
        assert (
            WalletTransaction.objects.filter(
                user=user, source_order=order, kind=WalletTransaction.KIND_REFERRAL_CREDIT
            ).count()
            == 1
        )


@pytest.mark.django_db
class TestClawback:
    def test_clawback_inverts_credit(self, user, another_user, order_factory):
        _set_referrer(referee=another_user, referrer=user)
        order = order_factory(customer=another_user, total=Decimal("100"))
        credit_referral(order)
        clawback = clawback_referral(order)
        assert clawback is not None
        assert clawback.amount == Decimal("-0.50")
        user.profile.refresh_from_db()
        assert user.profile.wallet_balance == Decimal("0.00")

    def test_clawback_idempotent(self, user, another_user, order_factory):
        _set_referrer(referee=another_user, referrer=user)
        order = order_factory(customer=another_user, total=Decimal("100"))
        credit_referral(order)
        first = clawback_referral(order)
        second = clawback_referral(order)
        assert first is not None
        assert second is None

    def test_clawback_without_credit_is_noop(self, user, another_user, order_factory):
        _set_referrer(referee=another_user, referrer=user)
        order = order_factory(customer=another_user, total=Decimal("100"))
        assert clawback_referral(order) is None


@pytest.mark.django_db
class TestSpendWallet:
    def test_debits_on_success(self, user, order_factory):
        manual_adjustment(user=user, amount=Decimal("10"), created_by=user, notes="seed")
        order = order_factory(customer=user, total=Decimal("25"))
        txn = spend_wallet(user, Decimal("3"), order)
        user.profile.refresh_from_db()
        assert txn.amount == Decimal("-3.00")
        assert user.profile.wallet_balance == Decimal("7.00")

    def test_zero_or_none_is_noop(self, user, order_factory):
        order = order_factory(customer=user, total=Decimal("25"))
        assert spend_wallet(user, Decimal("0"), order) is None

    def test_idempotent_per_order(self, user, order_factory):
        manual_adjustment(user=user, amount=Decimal("10"), created_by=user, notes="seed")
        order = order_factory(customer=user, total=Decimal("25"))
        first = spend_wallet(user, Decimal("3"), order)
        second = spend_wallet(user, Decimal("3"), order)
        assert first.id == second.id
        user.profile.refresh_from_db()
        assert user.profile.wallet_balance == Decimal("7.00")

    def test_raises_on_overdraft(self, user, order_factory):
        order = order_factory(customer=user, total=Decimal("25"))
        with pytest.raises(InsufficientBalance):
            spend_wallet(user, Decimal("3"), order)


@pytest.mark.django_db
class TestRefundWalletSpend:
    def test_returns_funds(self, user, order_factory):
        manual_adjustment(user=user, amount=Decimal("10"), created_by=user, notes="seed")
        order = order_factory(customer=user, total=Decimal("25"))
        spend_wallet(user, Decimal("3"), order)
        refund = refund_wallet_spend(order)
        assert refund.amount == Decimal("3.00")
        user.profile.refresh_from_db()
        assert user.profile.wallet_balance == Decimal("10.00")


@pytest.mark.django_db
class TestRecalculateBalance:
    def test_aggregates_to_cached_total(self, user):
        manual_adjustment(user=user, amount=Decimal("5"), created_by=user)
        manual_adjustment(user=user, amount=Decimal("-2"), created_by=user)
        balance = recalculate_balance(user)
        assert balance == Decimal("3.00")
        user.profile.refresh_from_db()
        assert user.profile.wallet_balance == Decimal("3.00")
