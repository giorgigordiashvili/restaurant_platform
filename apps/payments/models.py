"""
Payment models for restaurant payment processing.
"""

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Payment(TimeStampedModel):
    """
    Payment record for an order.
    Supports multiple payments per order (split bills).
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("refunded", "Refunded"),
        ("partially_refunded", "Partially Refunded"),
        ("cancelled", "Cancelled"),
    ]

    PAYMENT_METHOD_CHOICES = [
        ("card", "Card"),
        ("cash", "Cash"),
        ("mobile", "Mobile Payment"),
        ("voucher", "Voucher"),
        ("other", "Other"),
    ]

    # Relationships
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="payments",
    )
    customer = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    processed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_payments",
        help_text="Staff member who processed this payment",
    )

    # Payment details
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    tip_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Amount + tip",
    )

    # Payment method
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default="card",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    # External payment info (for card payments via Stripe, etc.)
    external_payment_id = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text="Payment ID from external provider (Stripe, etc.)",
    )
    external_payment_method_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Payment method ID from external provider",
    )
    payment_intent_id = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text="Stripe PaymentIntent ID",
    )

    # Metadata
    currency = models.CharField(max_length=3, default="USD")
    receipt_number = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
    )
    notes = models.TextField(blank=True)

    # Timestamps
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)

    class Meta:
        db_table = "payments"
        ordering = ["-created_at"]
        verbose_name = _("Payment")
        verbose_name_plural = _("Payments")
        indexes = [
            models.Index(fields=["order", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"Payment {self.id} - {self.total_amount} {self.currency}"

    def save(self, *args, **kwargs):
        # Calculate total amount
        self.total_amount = self.amount + self.tip_amount

        # Generate receipt number if not set
        if not self.receipt_number and self.status == "completed":
            self.receipt_number = self._generate_receipt_number()

        super().save(*args, **kwargs)

    def _generate_receipt_number(self) -> str:
        """Generate a unique receipt number."""
        from django.utils import timezone

        today = timezone.localdate()
        prefix = today.strftime("%y%m%d")

        count = Payment.objects.filter(
            order__restaurant=self.order.restaurant,
            created_at__date=today,
            receipt_number__startswith=f"RCP-{prefix}",
        ).count()

        return f"RCP-{prefix}-{count + 1:04d}"

    def complete(self):
        """Mark payment as completed."""
        from django.utils import timezone

        self.status = "completed"
        self.completed_at = timezone.now()
        if not self.receipt_number:
            self.receipt_number = self._generate_receipt_number()
        self.save(update_fields=["status", "completed_at", "receipt_number", "updated_at"])

    def fail(self, reason: str = ""):
        """Mark payment as failed."""
        from django.utils import timezone

        self.status = "failed"
        self.failed_at = timezone.now()
        self.failure_reason = reason
        self.save(update_fields=["status", "failed_at", "failure_reason", "updated_at"])

    def cancel(self):
        """Cancel the payment."""
        self.status = "cancelled"
        self.save(update_fields=["status", "updated_at"])

    @property
    def is_refundable(self) -> bool:
        """Check if payment can be refunded."""
        return self.status in ["completed", "partially_refunded"]

    @property
    def refunded_amount(self):
        """Get total refunded amount."""
        from django.db.models import Sum

        return self.refunds.filter(status="completed").aggregate(total=Sum("amount"))["total"] or 0

    @property
    def refundable_amount(self):
        """Get amount that can still be refunded."""
        return self.total_amount - self.refunded_amount


class Refund(TimeStampedModel):
    """
    Refund record for a payment.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    REASON_CHOICES = [
        ("customer_request", "Customer Request"),
        ("order_cancelled", "Order Cancelled"),
        ("item_unavailable", "Item Unavailable"),
        ("quality_issue", "Quality Issue"),
        ("wrong_order", "Wrong Order"),
        ("other", "Other"),
    ]

    payment = models.ForeignKey(
        Payment,
        on_delete=models.CASCADE,
        related_name="refunds",
    )
    processed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_refunds",
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    reason = models.CharField(
        max_length=30,
        choices=REASON_CHOICES,
        default="customer_request",
    )
    reason_details = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    # External refund info
    external_refund_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Refund ID from external provider",
    )

    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)

    class Meta:
        db_table = "refunds"
        ordering = ["-created_at"]
        verbose_name = _("Refund")
        verbose_name_plural = _("Refunds")

    def __str__(self):
        return f"Refund {self.id} - {self.amount} for Payment {self.payment_id}"

    def complete(self):
        """Mark refund as completed."""
        from django.utils import timezone

        self.status = "completed"
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at", "updated_at"])

        # Update payment status
        if self.payment.refunded_amount >= self.payment.total_amount:
            self.payment.status = "refunded"
        else:
            self.payment.status = "partially_refunded"
        self.payment.save(update_fields=["status", "updated_at"])

    def fail(self, reason: str = ""):
        """Mark refund as failed."""
        from django.utils import timezone

        self.status = "failed"
        self.failed_at = timezone.now()
        self.failure_reason = reason
        self.save(update_fields=["status", "failed_at", "failure_reason", "updated_at"])


class PaymentMethod(TimeStampedModel):
    """
    Saved payment method for a customer.
    """

    TYPE_CHOICES = [
        ("card", "Card"),
        ("bank_account", "Bank Account"),
    ]

    customer = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="payment_methods",
    )

    # Payment method details
    method_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default="card",
    )
    is_default = models.BooleanField(default=False)

    # External info
    external_method_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Payment method ID from external provider",
    )
    external_customer_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Customer ID from external provider",
    )

    # Card details (masked)
    card_brand = models.CharField(max_length=20, blank=True)
    card_last4 = models.CharField(max_length=4, blank=True)
    card_exp_month = models.PositiveSmallIntegerField(null=True, blank=True)
    card_exp_year = models.PositiveSmallIntegerField(null=True, blank=True)

    # Status
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "payment_methods"
        ordering = ["-is_default", "-created_at"]

    def __str__(self):
        if self.method_type == "card" and self.card_last4:
            return f"{self.card_brand} ****{self.card_last4}"
        return f"Payment Method {self.id}"

    def save(self, *args, **kwargs):
        # Ensure only one default per customer
        if self.is_default:
            PaymentMethod.objects.filter(customer=self.customer, is_default=True).exclude(id=self.id).update(
                is_default=False
            )
        super().save(*args, **kwargs)

    def set_as_default(self):
        """Set this payment method as the default."""
        self.is_default = True
        self.save(update_fields=["is_default", "updated_at"])

    def deactivate(self):
        """Deactivate this payment method."""
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])


class BogTransaction(TimeStampedModel):
    """
    Authoritative record of a Bank of Georgia Payment Manager order.

    One row per BOG ``order_id``. Links back to an Order, Reservation, or
    PaymentMethod depending on what the flow was initiating. The webhook handler
    mutates ``status`` and fans out to update the linked record.
    """

    FLOW_ORDER = "order"
    FLOW_RESERVATION = "reservation"
    FLOW_ADD_CARD = "add_card"
    FLOW_CHOICES = [
        (FLOW_ORDER, "Order"),
        (FLOW_RESERVATION, "Reservation"),
        (FLOW_ADD_CARD, "Add Card"),
    ]

    # BOG's own order_status.key values — stored verbatim so downstream code
    # can branch without a translation layer.
    STATUS_CREATED = "created"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_REJECTED = "rejected"
    STATUS_AUTH_REQUESTED = "auth_requested"
    STATUS_BLOCKED = "blocked"
    STATUS_PARTIAL_COMPLETED = "partial_completed"
    STATUS_REFUND_REQUESTED = "refund_requested"
    STATUS_REFUNDED = "refunded"
    STATUS_REFUNDED_PARTIALLY = "refunded_partially"
    STATUS_CHOICES = [
        (STATUS_CREATED, "Created"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_AUTH_REQUESTED, "Pre-auth Requested"),
        (STATUS_BLOCKED, "Pre-auth Blocked"),
        (STATUS_PARTIAL_COMPLETED, "Partially Completed"),
        (STATUS_REFUND_REQUESTED, "Refund Requested"),
        (STATUS_REFUNDED, "Refunded"),
        (STATUS_REFUNDED_PARTIALLY, "Refunded Partially"),
    ]

    bog_order_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="UUID returned by BOG when the order was created.",
    )
    external_order_id = models.CharField(
        max_length=64,
        db_index=True,
        blank=True,
        help_text="Our reference (Order.order_number, Reservation.confirmation_code, or method_intent_id).",
    )

    flow_type = models.CharField(
        max_length=20,
        choices=FLOW_CHOICES,
        default=FLOW_ORDER,
        db_index=True,
    )

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bog_transactions",
    )
    reservation = models.ForeignKey(
        "reservations.Reservation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bog_transactions",
    )
    payment_method = models.ForeignKey(
        "payments.PaymentMethod",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bog_transactions",
        help_text="Populated when this transaction tokenised a card (add_card flow).",
    )
    initiated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bog_transactions",
    )

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    currency = models.CharField(max_length=3, default="GEL")

    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_CREATED,
        db_index=True,
    )
    code = models.IntegerField(
        null=True,
        blank=True,
        help_text="BOG payment_detail.code (e.g. 100 = success, 107 = insufficient funds).",
    )
    code_description = models.TextField(blank=True)
    reject_reason = models.TextField(blank=True)

    redirect_url = models.URLField(max_length=1024, blank=True)
    return_url = models.URLField(max_length=1024, blank=True)
    callback_url = models.URLField(max_length=1024, blank=True)

    # Full JSON snapshots from BOG — handy for debugging and for deferring parts of
    # the shape that we don't consume yet (e.g. basket, payment_detail.card_type).
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    last_webhook_payload = models.JSONField(default=dict, blank=True)

    last_webhook_at = models.DateTimeField(null=True, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "bog_transactions"
        ordering = ["-created_at"]
        verbose_name = "BOG Transaction"
        verbose_name_plural = "BOG Transactions"
        indexes = [
            models.Index(fields=["flow_type", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - admin str
        return f"BOG {self.flow_type} {self.bog_order_id} ({self.status})"

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            self.STATUS_COMPLETED,
            self.STATUS_REJECTED,
            self.STATUS_REFUNDED,
            self.STATUS_REFUNDED_PARTIALLY,
        }

    @property
    def is_successful(self) -> bool:
        return self.status in {self.STATUS_COMPLETED, self.STATUS_PARTIAL_COMPLETED}
