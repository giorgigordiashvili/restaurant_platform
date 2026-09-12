"""
Fiscal framework: the restaurant's VAT / legal profile and every numbered
fiscal document (receipt, refund receipt, waybill, invoice).

No provider is wired to a live authority yet: ``NullProvider`` numbers and
records documents locally (printed with a "non-fiscal" watermark) and the
RS.ge stub exports waybill XML. A real provider plugs in through
``apps.fiscal.providers`` without touching the rest.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class FiscalProfile(TimeStampedModel):
    PROVIDER_CHOICES = [
        ("none", "Not fiscalised (internal numbering)"),
        ("rsge_stub", "RS.ge (waybills only, stub)"),
    ]

    restaurant = models.OneToOneField("tenants.Restaurant", on_delete=models.CASCADE, related_name="fiscal_profile")
    vat_payer = models.BooleanField(default=False, help_text="Registered VAT payer (turnover above the threshold).")
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("18.00"))
    prices_include_vat = models.BooleanField(default=True, help_text="Menu prices are gross (VAT included).")
    legal_name = models.CharField(max_length=200, blank=True, default="", help_text="As registered with RS.")
    tax_id = models.CharField(
        max_length=11,
        blank=True,
        default="",
        validators=[RegexValidator(r"^(\d{9}(\d{2})?)?$", "Tax id is 9 or 11 digits.")],
        help_text="ს/კ — 9 digits (company) or 11 (individual).",
    )
    legal_address = models.CharField(max_length=300, blank=True, default="")
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default="none")
    credentials_encrypted = models.TextField(blank=True, default="")
    receipt_prefix = models.CharField(max_length=8, default="R")
    receipt_footer = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "fiscal_profiles"
        verbose_name = _("Fiscal profile")
        verbose_name_plural = _("Fiscal profiles")

    def __str__(self):
        return f"Fiscal profile of {self.restaurant_id}"

    @property
    def effective_vat_rate(self) -> Decimal:
        return self.vat_rate if self.vat_payer else Decimal("0")

    def set_credentials(self, data: dict) -> None:
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
        except Exception:
            return {}


class FiscalDocument(TimeStampedModel):
    KIND_CHOICES = [
        ("receipt", "Receipt"),
        ("refund", "Refund receipt"),
        ("waybill_in", "Inbound waybill"),
        ("waybill_out", "Outbound waybill"),
        ("invoice", "E-invoice"),
    ]
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("queued", "Queued"),
        ("sent", "Sent"),
        ("confirmed", "Confirmed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.PROTECT, related_name="fiscal_documents")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True)
    provider = models.CharField(max_length=20, default="none")
    payment = models.ForeignKey(
        "payments.Payment", on_delete=models.SET_NULL, null=True, blank=True, related_name="fiscal_documents"
    )
    refund = models.ForeignKey(
        "payments.Refund", on_delete=models.SET_NULL, null=True, blank=True, related_name="fiscal_documents"
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="fiscal_documents"
    )
    stock_lot = models.ForeignKey(
        "inventory.StockLot", on_delete=models.SET_NULL, null=True, blank=True, related_name="fiscal_documents"
    )
    reverses = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reversed_by",
        help_text="Refund -> receipt",
    )
    fiscal_number = models.CharField(max_length=40, blank=True, default="", db_index=True)
    external_id = models.CharField(max_length=100, blank=True, default="", db_index=True)
    is_fiscal = models.BooleanField(default=False, help_text="False = recorded locally only (non-fiscal watermark).")
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    prices_include_vat = models.BooleanField(default=True)
    net_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    vat_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gross_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    vat_breakdown = models.JSONField(default=list, blank=True)
    lines = models.JSONField(default=list, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    issued_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "fiscal_documents"
        ordering = ["-created_at"]
        verbose_name = _("Fiscal document")
        verbose_name_plural = _("Fiscal documents")
        indexes = [
            models.Index(fields=["restaurant", "status"]),
            models.Index(fields=["restaurant", "kind", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant", "kind", "fiscal_number"],
                condition=~models.Q(fiscal_number=""),
                name="fiscal_number_unique_per_kind",
            ),
            models.UniqueConstraint(
                fields=["payment"],
                condition=models.Q(kind="receipt") & ~models.Q(status="cancelled"),
                name="one_receipt_per_payment",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.fiscal_number or self.pk} ({self.status})"


class FiscalSettingsPage(FiscalProfile):
    """Proxy: the 'Fiscal settings' page in the tenant admin sidebar."""

    class Meta:
        proxy = True
        verbose_name = "Fiscal settings"
        verbose_name_plural = "Fiscal settings"
