"""
Gift cards: a human-typeable code with a balance. Selling one is a payment
without an order; redeeming one is a ``gift_card`` payment on an order or a
table, so the ledger, shifts and reports stay consistent.
"""

from __future__ import annotations

import secrets
import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # Crockford-ish: no 0/O/1/I/L/U


def new_code() -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"GC-{body[:4]}-{body[4:]}"


def new_token() -> str:
    return secrets.token_urlsafe(16)


class GiftCard(TimeStampedModel):
    STATUS_CHOICES = [
        ("pending", _("Awaiting payment")),
        ("active", _("Active")),
        ("used_up", _("Used up")),
        ("void", _("Void")),
        ("expired", _("Expired")),
    ]
    KIND_CHOICES = [("physical", _("Physical card")), ("digital", _("Digital (SMS / email)"))]
    DESIGN_CHOICES = [
        ("classic", _("Classic")),
        ("birthday", _("Birthday")),
        ("thanks", _("Thank you")),
        ("holiday", _("Holiday")),
    ]

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="gift_cards")
    code = models.CharField(max_length=16, unique=True, default=new_code, db_index=True)
    initial_value = models.DecimalField(max_digits=10, decimal_places=2)
    balance = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="GEL")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="active", db_index=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="physical")
    design = models.CharField(max_length=12, choices=DESIGN_CHOICES, default="classic")
    expires_at = models.DateTimeField(null=True, blank=True)
    purchaser_name = models.CharField(max_length=120, blank=True, default="")
    purchaser_phone = models.CharField(max_length=20, blank=True, default="")
    purchaser_email = models.EmailField(blank=True, default="")
    recipient_name = models.CharField(max_length=120, blank=True, default="")
    recipient_phone = models.CharField(max_length=20, blank=True, default="")
    recipient_email = models.EmailField(blank=True, default="")
    message = models.CharField(max_length=300, blank=True, default="")
    sold_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    sold_payment = models.ForeignKey(
        "payments.Payment", on_delete=models.SET_NULL, null=True, blank=True, related_name="gift_cards_sold"
    )
    sold_online = models.BooleanField(default=False)
    token = models.CharField(max_length=40, default=new_token, unique=True, editable=False)
    delivered_at = models.DateTimeField(null=True, blank=True)
    notes = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "gift_cards"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["restaurant", "status", "created_at"])]
        verbose_name = _("Gift card")
        verbose_name_plural = _("Gift cards")

    def __str__(self):
        return f"{self.code} · {self.balance} {self.currency}"

    @property
    def masked_code(self) -> str:
        return f"{self.code[:3]}····{self.code[-4:]}"

    @property
    def is_usable(self) -> bool:
        from django.utils import timezone

        if self.status != "active" or self.balance <= 0:
            return False
        return not (self.expires_at and self.expires_at < timezone.now())


class GiftCardTransaction(TimeStampedModel):
    KIND_CHOICES = [
        ("issue", _("Issued")),
        ("redeem", _("Redeemed")),
        ("refund", _("Refunded to card")),
        ("adjust", _("Adjusted")),
        ("void", _("Voided")),
    ]

    card = models.ForeignKey(GiftCard, on_delete=models.CASCADE, related_name="transactions")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text=_("Signed: + adds to the balance."))
    balance_after = models.DecimalField(max_digits=10, decimal_places=2)
    payment = models.ForeignKey(
        "payments.Payment", on_delete=models.SET_NULL, null=True, blank=True, related_name="gift_card_transactions"
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="gift_card_transactions"
    )
    by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    note = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "gift_card_transactions"
        ordering = ["-created_at"]
        verbose_name = _("Gift card transaction")
        verbose_name_plural = _("Gift card transactions")

    def __str__(self):
        return f"{self.card.code} {self.get_kind_display()} {self.amount}"


class GiftCardBatchPage(GiftCard):
    """Proxy: 'sell a physical batch' page."""

    class Meta:
        proxy = True
        verbose_name = _("Physical card batch")
        verbose_name_plural = _("Physical card batch")
