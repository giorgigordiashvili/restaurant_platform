"""
House accounts (tabs on credit): regulars and companies eat now and settle
later. A charge is a ``house_account`` ledger payment on the bill; a
settlement is real money (cash / card) received against the account.
"""

from __future__ import annotations

import secrets

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


def new_token() -> str:
    return secrets.token_urlsafe(16)


class HouseAccount(TimeStampedModel):
    STATUS_CHOICES = [("active", _("Active")), ("suspended", _("Suspended")), ("closed", _("Closed"))]

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="house_accounts")
    customer = models.ForeignKey(
        "crm.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="house_accounts"
    )
    name = models.CharField(max_length=120, help_text=_("Person or company on the account."))
    company = models.CharField(max_length=120, blank=True, default="")
    tax_id = models.CharField(max_length=20, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="", db_index=True)
    email = models.EmailField(blank=True, default="")
    credit_limit = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text=_("0 = no limit."))
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text=_("Amount owed to you."))
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="active", db_index=True)
    billing_day = models.PositiveSmallIntegerField(default=1, help_text=_("Day of month the statement goes out."))
    authorised_names = models.CharField(
        max_length=300, blank=True, default="", help_text=_("Who may charge to this account (comma separated).")
    )
    require_signature = models.BooleanField(default=False, help_text=_("Ask who signed at the POS."))
    notes = models.TextField(blank=True, default="")
    token = models.CharField(max_length=40, default=new_token, unique=True, editable=False)
    last_payment_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "house_accounts"
        ordering = ["name"]
        indexes = [models.Index(fields=["restaurant", "status"])]
        verbose_name = _("House account")
        verbose_name_plural = _("House accounts")

    def __str__(self):
        return f"{self.name}{' · ' + self.company if self.company else ''}"

    @property
    def available(self):
        if not self.credit_limit:
            return None
        return max(self.credit_limit - self.balance, 0)


class HouseAccountEntry(TimeStampedModel):
    KIND_CHOICES = [
        ("charge", _("Charge")),
        ("payment", _("Payment received")),
        ("adjustment", _("Adjustment")),
        ("writeoff", _("Written off")),
    ]

    account = models.ForeignKey(HouseAccount, on_delete=models.CASCADE, related_name="entries")
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text=_("Signed: + increases what is owed."))
    balance_after = models.DecimalField(max_digits=10, decimal_places=2)
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="house_account_entries"
    )
    payment = models.ForeignKey(
        "payments.Payment", on_delete=models.SET_NULL, null=True, blank=True, related_name="house_account_entries"
    )
    by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    signed_by = models.CharField(max_length=120, blank=True, default="")
    note = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "house_account_entries"
        ordering = ["-created_at"]
        verbose_name = _("House account entry")
        verbose_name_plural = _("House account entries")

    def __str__(self):
        return f"{self.account.name} {self.get_kind_display()} {self.amount}"


class HouseAccountStatement(TimeStampedModel):
    account = models.ForeignKey(HouseAccount, on_delete=models.CASCADE, related_name="statements")
    period_start = models.DateField()
    period_end = models.DateField()
    opening = models.DecimalField(max_digits=10, decimal_places=2)
    charges = models.DecimalField(max_digits=10, decimal_places=2)
    payments = models.DecimalField(max_digits=10, decimal_places=2)
    adjustments = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    closing = models.DecimalField(max_digits=10, decimal_places=2)
    lines = models.JSONField(default=list, blank=True)
    token = models.CharField(max_length=40, default=new_token, unique=True, editable=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_to = models.CharField(max_length=254, blank=True, default="")

    class Meta:
        db_table = "house_account_statements"
        ordering = ["-period_end"]
        unique_together = [("account", "period_start", "period_end")]
        verbose_name = _("Statement")
        verbose_name_plural = _("Statements")

    def __str__(self):
        return f"{self.account.name} {self.period_start} – {self.period_end}"
