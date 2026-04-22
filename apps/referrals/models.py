"""
Referrals app — wallet ledger backing the user-referral program.

`UserProfile.wallet_balance` is a cached running total. The truth lives in
`WalletTransaction`, an immutable ledger of every credit and debit. Reconciling
the cached balance from the ledger is a one-line aggregate (see
`apps.referrals.services.recalculate_balance`).
"""

from django.db import models

from apps.core.models import TimeStampedModel


class WalletTransaction(TimeStampedModel):
    """Immutable per-user ledger entry. Sign-aware: + credit, − debit."""

    KIND_REFERRAL_CREDIT = "referral_credit"
    KIND_ORDER_SPEND = "order_spend"
    KIND_REFUND_CREDIT = "refund_credit"
    KIND_REFERRAL_CLAWBACK = "referral_clawback"
    KIND_MANUAL_ADJUSTMENT = "manual_adjustment"

    KIND_CHOICES = [
        (KIND_REFERRAL_CREDIT, "Referral credit"),
        (KIND_ORDER_SPEND, "Order spend"),
        (KIND_REFUND_CREDIT, "Refund credit"),
        (KIND_REFERRAL_CLAWBACK, "Referral clawback"),
        (KIND_MANUAL_ADJUSTMENT, "Manual adjustment"),
    ]

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="wallet_transactions",
        help_text="Owner of the wallet being credited / debited.",
    )
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, db_index=True)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Signed: positive for credit, negative for debit.",
    )
    balance_after = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Snapshot of user.profile.wallet_balance immediately after this row was written.",
    )
    source_order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wallet_transactions",
        help_text="Order this credit / debit relates to (if any).",
    )
    referred_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referral_credits_triggered",
        help_text=("For referral_credit / referral_clawback: the referred user whose order " "triggered this row."),
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wallet_transactions_created",
        help_text="Admin who created this row, for manual_adjustment kinds.",
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "wallet_transactions"
        ordering = ["-created_at"]
        verbose_name = "Wallet transaction"
        verbose_name_plural = "Wallet transactions"
        indexes = [
            models.Index(fields=["user", "-created_at"], name="wallet_user_created_idx"),
            models.Index(fields=["kind"], name="wallet_kind_idx"),
        ]

    def __str__(self):
        return f"{self.kind} {self.amount} → {self.user_id}"
