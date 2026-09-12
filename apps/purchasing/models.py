"""
Suppliers, their price lists and purchase orders. Receiving a PO books stock
lots through the warehouse (``apps.inventory.services.receive_stock``), so
costs, expiry and FIFO stay in one place.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Supplier(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="suppliers")
    name = models.CharField(max_length=150)
    contact_name = models.CharField(max_length=120, blank=True, default="")
    phone = models.CharField(max_length=30, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    tax_id = models.CharField(max_length=20, blank=True, default="", help_text=_("ს/კ of the supplier (waybills)."))
    address = models.CharField(max_length=300, blank=True, default="")
    payment_terms = models.CharField(
        max_length=120, blank=True, default="", help_text=_("E.g. 'on delivery', '14 days'.")
    )
    lead_days = models.PositiveSmallIntegerField(default=1, help_text=_("Days between ordering and delivery."))
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "purchasing_suppliers"
        ordering = ["name"]
        unique_together = [("restaurant", "name")]
        verbose_name = _("Supplier")
        verbose_name_plural = _("Suppliers")

    def __str__(self):
        return self.name


class SupplierItem(TimeStampedModel):
    """What a supplier sells us and at what price (per the purchase unit)."""

    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="items")
    stock_item = models.ForeignKey("inventory.StockItem", on_delete=models.CASCADE, related_name="supplier_items")
    supplier_sku = models.CharField(max_length=60, blank=True, default="")
    unit = models.ForeignKey(
        "inventory.UnitOfMeasure", on_delete=models.PROTECT, related_name="+", help_text=_("Unit the price is per.")
    )
    pack_qty = models.DecimalField(
        max_digits=12, decimal_places=4, default=1, help_text=_("Units per pack (a 10 kg bag = 10).")
    )
    price = models.DecimalField(max_digits=12, decimal_places=4, default=0, help_text=_("Per unit, latest known."))
    last_price_at = models.DateTimeField(null=True, blank=True)
    is_preferred = models.BooleanField(default=False, help_text=_("The buy list orders from this supplier."))

    class Meta:
        db_table = "purchasing_supplier_items"
        unique_together = [("supplier", "stock_item")]
        verbose_name = _("Supplier price")
        verbose_name_plural = _("Supplier prices")

    def __str__(self):
        return f"{self.stock_item} @ {self.supplier}: {self.price}/{self.unit_id}"


class PriceObservation(TimeStampedModel):
    supplier_item = models.ForeignKey(SupplierItem, on_delete=models.CASCADE, related_name="history")
    price = models.DecimalField(max_digits=12, decimal_places=4)
    source = models.CharField(max_length=20, default="receive")  # receive | manual | order
    observed_at = models.DateTimeField()

    class Meta:
        db_table = "purchasing_price_history"
        ordering = ["-observed_at"]


class PurchaseOrder(TimeStampedModel):
    STATUS_CHOICES = [
        ("draft", _("Draft")),
        ("sent", _("Sent to supplier")),
        ("partial", _("Partially received")),
        ("received", _("Received")),
        ("cancelled", _("Cancelled")),
    ]
    OPEN = ("draft", "sent", "partial")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="purchase_orders")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, null=True, blank=True, related_name="orders")
    number = models.CharField(max_length=30)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="draft", db_index=True)
    expected_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="", help_text=_("Shown to the supplier."))
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_via = models.CharField(max_length=20, blank=True, default="")
    received_at = models.DateTimeField(null=True, blank=True)
    reference = models.CharField(max_length=100, blank=True, default="", help_text=_("Supplier invoice / waybill no."))

    class Meta:
        db_table = "purchasing_orders"
        ordering = ["-created_at"]
        unique_together = [("restaurant", "number")]
        verbose_name = _("Purchase order")
        verbose_name_plural = _("Purchase orders")

    def __str__(self):
        return self.number

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN

    def recompute(self, save=True):
        total = sum((line.line_total for line in self.lines.all()), Decimal("0"))
        self.subtotal = total.quantize(Decimal("0.01"))
        if save:
            self.save(update_fields=["subtotal", "updated_at"])
        return self.subtotal


class PurchaseOrderLine(TimeStampedModel):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    stock_item = models.ForeignKey("inventory.StockItem", on_delete=models.PROTECT, related_name="po_lines")
    supplier_item = models.ForeignKey(
        SupplierItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="po_lines"
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"))])
    unit = models.ForeignKey("inventory.UnitOfMeasure", on_delete=models.PROTECT, related_name="+")
    unit_price = models.DecimalField(max_digits=12, decimal_places=4, default=0, help_text=_("Per unit."))
    received_qty = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    note = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "purchasing_order_lines"
        ordering = ["created_at"]
        verbose_name = _("Purchase order line")
        verbose_name_plural = _("Purchase order lines")

    def __str__(self):
        return f"{self.quantity} {self.unit_id} {self.stock_item}"

    @property
    def line_total(self) -> Decimal:
        return (Decimal(self.quantity or 0) * Decimal(self.unit_price or 0)).quantize(Decimal("0.01"))

    @property
    def outstanding(self) -> Decimal:
        return max(Decimal(self.quantity or 0) - Decimal(self.received_qty or 0), Decimal("0"))
