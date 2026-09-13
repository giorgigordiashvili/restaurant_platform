"""
Card terminals & pay-by-link.

A ``PaymentTerminal`` is one way of taking a card payment: the cashier
confirms on a physical terminal (``manual``), a BOG / TBC payment link + QR
the guest pays on their phone (``bog_link`` / ``tbc_tpay``), or an ECR
terminal driven through the terminal bridge on the till PC (``ecr_bridge``).
A ``TerminalTransaction`` is one attempt; when it is approved it becomes a
ledger ``Payment`` through ``record_payment`` so shifts, receipts, fiscal
documents and reports see it like any other card payment.
"""

from __future__ import annotations

import secrets
import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


def new_bridge_key() -> str:
    return secrets.token_urlsafe(24)


class PaymentTerminal(TimeStampedModel):
    PROVIDER_CHOICES = [
        ("manual", _("Physical terminal (cashier confirms)")),
        ("bog_link", _("Bank of Georgia pay-by-link / QR")),
        ("tbc_tpay", _("TBC pay-by-link / QR (TPAY)")),
        ("ecr_bridge", _("ECR terminal via the terminal bridge")),
    ]
    ECR_PROTOCOL_CHOICES = [("", _("—")), ("bog", _("Bank of Georgia ECR")), ("tbc", _("TBC ECR"))]
    LINK_PROVIDERS = ("bog_link", "tbc_tpay")

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="terminals")
    name = models.CharField(max_length=100, help_text=_("E.g. 'Till 1', 'Bar terminal', 'QR at the table'."))
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default="manual")
    ecr_protocol = models.CharField(max_length=10, choices=ECR_PROTOCOL_CHOICES, blank=True, default="")
    terminal_id = models.CharField(max_length=60, blank=True, default="", help_text=_("The bank's terminal id (TID)."))
    connection = models.JSONField(
        default=dict, blank=True, help_text=_('Bridge device: {"device": "tcp://192.168.1.60:8000"} or serial://.')
    )
    credentials_encrypted = models.TextField(blank=True, default="")
    bridge_key = models.CharField(max_length=64, unique=True, default=new_bridge_key, editable=False)
    last_seen_at = models.DateTimeField(null=True, blank=True, help_text=_("Last poll from the bridge."))
    last_error = models.CharField(max_length=300, blank=True, default="")
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, help_text=_("Preselected in the POS."))
    auto_receipt = models.BooleanField(default=True, help_text=_("Print a receipt when the payment is approved."))
    timeout_seconds = models.PositiveSmallIntegerField(default=180, help_text=_("Give up waiting after this long."))

    class Meta:
        db_table = "terminals"
        ordering = ["-is_default", "name"]
        verbose_name = _("Card terminal")
        verbose_name_plural = _("Card terminals")

    def __str__(self):
        return f"{self.name} ({self.get_provider_display()})"

    @property
    def is_link(self) -> bool:
        return self.provider in self.LINK_PROVIDERS

    @property
    def is_online(self) -> bool:
        if self.provider != "ecr_bridge":
            return True
        return bool(self.last_seen_at) and timezone.now() - self.last_seen_at < timezone.timedelta(minutes=2)

    def set_credentials(self, data: dict):
        import json

        from apps.core.utils.encryption import encrypt_field

        self.credentials_encrypted = encrypt_field(json.dumps(data)) if data else ""

    def get_credentials(self) -> dict:
        import json

        from apps.core.utils.encryption import decrypt_field

        if not self.credentials_encrypted:
            return {}
        try:
            return json.loads(decrypt_field(self.credentials_encrypted))
        except ValueError:
            return {}

    def rotate_key(self):
        self.bridge_key = new_bridge_key()
        self.save(update_fields=["bridge_key", "updated_at"])


class TerminalTransaction(TimeStampedModel):
    KIND_CHOICES = [("sale", _("Sale")), ("refund", _("Refund"))]
    STATUS_CHOICES = [
        ("pending", _("Starting")),
        ("sent", _("Sent to terminal")),
        ("awaiting_confirm", _("Waiting for confirmation")),
        ("approved", _("Approved")),
        ("declined", _("Declined")),
        ("cancelled", _("Cancelled")),
        ("timeout", _("Timed out")),
        ("failed", _("Failed")),
    ]
    OPEN = ("pending", "sent", "awaiting_confirm")
    FINAL = ("approved", "declined", "cancelled", "timeout", "failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="terminal_transactions")
    terminal = models.ForeignKey(PaymentTerminal, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="sale")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    tip = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="GEL")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="terminal_transactions"
    )
    session = models.ForeignKey(
        "tables.TableSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="terminal_transactions"
    )
    order_ids = models.JSONField(default=list, blank=True)
    payment = models.ForeignKey(
        "payments.Payment", on_delete=models.SET_NULL, null=True, blank=True, related_name="terminal_transactions"
    )
    refund_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="refunds")
    external_id = models.CharField(max_length=120, blank=True, default="", db_index=True)
    auth_code = models.CharField(max_length=40, blank=True, default="")
    card_mask = models.CharField(max_length=30, blank=True, default="")
    rrn = models.CharField(max_length=40, blank=True, default="")
    pay_url = models.URLField(max_length=600, blank=True, default="")
    request = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=300, blank=True, default="")
    initiated_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    confirmed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    sent_to = models.CharField(max_length=254, blank=True, default="", help_text=_("Phone / email the link went to."))
    expires_at = models.DateTimeField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "terminal_transactions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["terminal", "status", "created_at"]),
            models.Index(fields=["restaurant", "status", "created_at"]),
        ]
        verbose_name = _("Terminal transaction")
        verbose_name_plural = _("Terminal transactions")

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount} {self.currency} · {self.get_status_display()}"

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN

    @property
    def total(self):
        return (self.amount or 0) + (self.tip or 0)

    @property
    def target_label(self) -> str:
        if self.order_id:
            return self.order.order_number
        if self.session_id:
            return f"Table {self.session.table.number}" if self.session.table_id else "Table"
        return ", ".join(str(i)[:8] for i in self.order_ids)


class TerminalReconciliation(TerminalTransaction):
    """Proxy: the per-day 'card payments vs terminal approvals' page."""

    class Meta:
        proxy = True
        verbose_name = _("Terminal reconciliation")
        verbose_name_plural = _("Terminal reconciliation")
