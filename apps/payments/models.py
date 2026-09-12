"""
Payment models for restaurant payment processing.
"""

import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

# ---------------------------------------------------------------------------
# Cash ledger: shifts, counters, reasons, allocations
# ---------------------------------------------------------------------------


class ReceiptSequence(models.Model):
    """
    Locked per-restaurant counter. ``kind`` names the series (``payment_daily``
    for RCP- numbers; the fiscal module adds ``receipt``, ``refund`` ...) and
    ``period`` scopes it ("" = continuous, ``YYMMDD`` = restarts daily).
    """

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="receipt_sequences")
    kind = models.CharField(max_length=30)
    period = models.CharField(max_length=10, blank=True, default="")
    last = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "receipt_sequences"
        unique_together = [("restaurant", "kind", "period")]

    def __str__(self):  # pragma: no cover
        return f"{self.kind}/{self.period or 'all'}: {self.last}"


class CashShift(TimeStampedModel):
    """
    One till session: opened with a float, closed with a count. Every payment
    taken while it is open is attached to it, so the Z report reconciles the
    drawer. One open shift per restaurant (``register`` is reserved for a
    per-drawer split later).
    """

    STATUS_CHOICES = [("open", "Open"), ("closed", "Closed")]

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="cash_shifts")
    number = models.PositiveIntegerField(help_text="Sequential per restaurant.")
    register = models.CharField(max_length=50, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="open", db_index=True)
    opened_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="opened_shifts"
    )
    opened_at = models.DateTimeField()
    closed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="closed_shifts"
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    opening_float = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    counted_cash = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    expected_cash = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    difference = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="counted - expected"
    )
    report = models.JSONField(default=dict, blank=True, help_text="Z report frozen at close.")
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "cash_shifts"
        ordering = ["-opened_at"]
        verbose_name = _("Cash shift")
        verbose_name_plural = _("Cash shifts")
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant"], condition=models.Q(status="open"), name="one_open_cash_shift_per_restaurant"
            ),
            models.UniqueConstraint(fields=["restaurant", "number"], name="cash_shift_number_unique"),
        ]
        indexes = [models.Index(fields=["restaurant", "status"])]

    def __str__(self):
        return f"Shift #{self.number} ({self.status})"

    @property
    def is_open(self) -> bool:
        return self.status == "open"


class CashMovement(TimeStampedModel):
    """Cash paid into or taken out of the drawer outside of sales (float top-up, supplier paid in cash ...)."""

    KIND_CHOICES = [("paid_in", "Paid in"), ("paid_out", "Paid out")]

    shift = models.ForeignKey(CashShift, on_delete=models.CASCADE, related_name="movements")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    reason = models.CharField(max_length=200)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="cash_movements"
    )

    class Meta:
        db_table = "cash_movements"
        ordering = ["created_at"]

    def __str__(self):  # pragma: no cover
        return f"{self.get_kind_display()} {self.amount}"


class DiscountReason(TimeStampedModel):
    """Reasons staff pick when discounting, comping, voiding or refunding."""

    KIND_CHOICES = [
        ("discount", "Discount"),
        ("comp", "Comp (on the house)"),
        ("void", "Void"),
        ("refund", "Refund"),
    ]

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="discount_reasons")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, db_index=True)
    label = models.CharField(max_length=100)
    requires_manager = models.BooleanField(
        default=False, help_text="Only roles with 'Cash & payments: edit' may use it."
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "discount_reasons"
        ordering = ["kind", "sort_order", "label"]
        verbose_name = _("Discount / void reason")
        verbose_name_plural = _("Discount / void reasons")

    def __str__(self):
        return f"{self.label} ({self.get_kind_display()})"


class PaymentAllocation(models.Model):
    """How much of a payment settles which order. A payment may cover several orders (a table) and an order may be paid in parts."""

    payment = models.ForeignKey("payments.Payment", on_delete=models.CASCADE, related_name="allocations")
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="payment_allocations")
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])

    class Meta:
        db_table = "payment_allocations"
        unique_together = [("payment", "order")]

    def __str__(self):  # pragma: no cover
        return f"{self.amount} of {self.payment_id} -> {self.order_id}"


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
        ("cash", "Cash"),
        ("card_terminal", "Card (terminal)"),
        ("online_bog", "Online (Bank of Georgia)"),
        ("online_flitt", "Online (Flitt)"),
        ("voucher", "Voucher"),
        ("other", "Other"),
        # Legacy values kept for rows written before the ledger existed.
        ("card", "Card"),
        ("mobile", "Mobile Payment"),
    ]
    STAFF_METHODS = ("cash", "card_terminal", "voucher", "other")
    ONLINE_METHODS = ("online_bog", "online_flitt", "card", "mobile")

    # Relationships
    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="payments",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="payments",
        null=True,
        blank=True,
        help_text="The (first) order this payment settles; the full split is in the allocations.",
    )
    session = models.ForeignKey(
        "tables.TableSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    covered_orders = models.ManyToManyField(
        "orders.Order",
        through="payments.PaymentAllocation",
        related_name="covering_payments",
        blank=True,
    )
    shift = models.ForeignKey(
        "payments.CashShift",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
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

    tendered = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Cash handed over by the customer (cash payments).",
    )
    change_given = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Metadata
    currency = models.CharField(max_length=3, default="GEL")
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
            models.Index(fields=["restaurant", "status", "completed_at"]),
            models.Index(fields=["shift", "status"]),
        ]

    def __str__(self):
        return f"Payment {self.id} - {self.total_amount} {self.currency}"

    def save(self, *args, **kwargs):
        # Calculate total amount
        self.total_amount = (self.amount or Decimal("0")) + (self.tip_amount or Decimal("0"))

        # Older call sites create payments from an order only.
        if self.restaurant_id is None and self.order_id:
            self.restaurant_id = self.order.restaurant_id

        # Generate receipt number if not set
        if not self.receipt_number and self.status == "completed":
            self.receipt_number = self._generate_receipt_number()

        super().save(*args, **kwargs)

    def _generate_receipt_number(self) -> str:
        """Race-free daily receipt number (RCP-YYMMDD-NNNN) from the locked counter."""
        from apps.payments.services import next_receipt_number

        restaurant_id = self.restaurant_id or (self.order.restaurant_id if self.order_id else None)
        return next_receipt_number(restaurant_id)

    def complete(self):
        """Mark payment as completed."""
        from django.utils import timezone

        self.status = "completed"
        self.completed_at = timezone.now()
        if not self.receipt_number:
            self.receipt_number = self._generate_receipt_number()
        self.save(update_fields=["status", "completed_at", "receipt_number", "updated_at"])
        from apps.fiscal import hooks as fiscal_hooks

        fiscal_hooks.on_payment_completed(self)

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

    METHOD_CHOICES = [
        ("cash", "Cash"),
        ("card_terminal", "Card (terminal)"),
        ("online", "Online provider"),
        ("voucher", "Voucher"),
        ("other", "Other"),
    ]

    payment = models.ForeignKey(
        Payment,
        on_delete=models.CASCADE,
        related_name="refunds",
    )
    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="refunds",
        null=True,
        blank=True,
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="refunds",
        help_text="Order the refunded amount is taken off (for paid/balance figures).",
    )
    shift = models.ForeignKey(
        "payments.CashShift",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="refunds",
    )
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default="cash")
    reason_code = models.ForeignKey(
        "payments.DiscountReason",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
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

    def save(self, *args, **kwargs):
        if self.restaurant_id is None and self.payment_id:
            self.restaurant_id = self.payment.restaurant_id
        super().save(*args, **kwargs)

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
        from apps.fiscal import hooks as fiscal_hooks

        fiscal_hooks.on_refund_completed(self)

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
    FLOW_SESSION_SETTLE = "session_settle"
    # Staff-recorded cash payment — no BOG round-trip, just a ledger entry
    # so the unpaid-orders guard on session close is satisfied and the
    # money is auditable. The `bog_order_id` is synthesised with a
    # `CASH-` prefix to stay within the unique constraint.
    FLOW_CASH_SETTLE = "cash_settle"
    FLOW_CHOICES = [
        (FLOW_ORDER, "Order"),
        (FLOW_RESERVATION, "Reservation"),
        (FLOW_ADD_CARD, "Add Card"),
        (FLOW_SESSION_SETTLE, "Session Settle"),
        (FLOW_CASH_SETTLE, "Cash Settle"),
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
    session = models.ForeignKey(
        "tables.TableSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bog_transactions",
        help_text="Populated when this transaction settles a whole table (session_settle flow).",
    )
    covered_orders = models.ManyToManyField(
        "orders.Order",
        related_name="settle_transactions",
        blank=True,
        help_text=(
            "Orders this transaction pays off in bulk (session_settle flow). "
            "Snapshotted at initiate-time so orders placed after the settle "
            "call still require their own payment."
        ),
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


class FlittTransaction(TimeStampedModel):
    """
    Ledger entry for every Flitt (pay.flitt.com) interaction — the Flitt
    counterpart to :class:`BogTransaction`.

    We keep the two provider ledgers as sibling models rather than one
    polymorphic table. Each provider has materially different shape (Flitt
    needs a two-step `/api/settlement` call the platform owns, BOG does the
    split inline), and collapsing them under a `provider` discriminator
    would bury those differences behind nullable fields.
    """

    FLOW_ORDER = "order"
    FLOW_RESERVATION = "reservation"
    FLOW_SESSION_SETTLE = "session_settle"
    FLOW_ADD_CARD = "add_card"
    FLOW_CHOICES = [
        (FLOW_ORDER, "Order"),
        (FLOW_RESERVATION, "Reservation"),
        (FLOW_SESSION_SETTLE, "Session settle"),
        (FLOW_ADD_CARD, "Add card"),
    ]

    # Checkout status — mirrors Flitt's own `order_status` values.
    STATUS_CREATED = "created"
    STATUS_PROCESSING = "processing"
    STATUS_APPROVED = "approved"
    STATUS_DECLINED = "declined"
    STATUS_EXPIRED = "expired"
    STATUS_REVERSED = "reversed"
    STATUS_REVERSED_PARTIALLY = "reversed_partially"
    STATUS_CHOICES = [
        (STATUS_CREATED, "Created"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_REVERSED, "Reversed"),
        (STATUS_REVERSED_PARTIALLY, "Reversed partially"),
    ]

    # Settlement status — second-step POST /api/settlement.
    SETTLEMENT_NOT_STARTED = "not_started"
    SETTLEMENT_PENDING = "pending"
    SETTLEMENT_SETTLED = "settled"
    SETTLEMENT_ERROR = "error"
    SETTLEMENT_CHOICES = [
        (SETTLEMENT_NOT_STARTED, "Not started"),
        (SETTLEMENT_PENDING, "Pending retry"),
        (SETTLEMENT_SETTLED, "Settled"),
        (SETTLEMENT_ERROR, "Error"),
    ]

    # Flitt returns an integer payment_id; we persist as string to keep the
    # model provider-neutral (BogTransaction also uses CharField for the id).
    flitt_payment_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    flitt_order_id = models.CharField(max_length=64, unique=True)

    flow_type = models.CharField(max_length=20, choices=FLOW_CHOICES, db_index=True)
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_CREATED,
        db_index=True,
    )

    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="flitt_transactions",
    )
    reservation = models.ForeignKey(
        "reservations.Reservation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="flitt_transactions",
    )
    session = models.ForeignKey(
        "tables.TableSession",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="flitt_transactions",
    )
    covered_orders = models.ManyToManyField(
        "orders.Order",
        blank=True,
        related_name="flitt_settle_transactions",
    )
    initiated_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="GEL")

    checkout_url = models.URLField(max_length=1024, blank=True, default="")
    return_url = models.URLField(max_length=1024, blank=True, default="")
    callback_url = models.URLField(max_length=1024, blank=True, default="")

    # Payload snapshots — invaluable when debugging a Flitt support ticket.
    request_payload = models.JSONField(null=True, blank=True)
    response_payload = models.JSONField(null=True, blank=True)
    last_webhook_payload = models.JSONField(null=True, blank=True)
    last_webhook_at = models.DateTimeField(null=True, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)

    # Settlement (the second-step /api/settlement call that fans the
    # approved amount out to the platform + restaurant sub-merchants).
    settlement_status = models.CharField(
        max_length=16,
        choices=SETTLEMENT_CHOICES,
        default=SETTLEMENT_NOT_STARTED,
        db_index=True,
    )
    settlement_id = models.CharField(max_length=64, blank=True, default="")
    settlement_error = models.TextField(blank=True, default="")
    settled_at = models.DateTimeField(null=True, blank=True)
    # Split snapshot frozen at settlement time — retained verbatim so refund
    # /api/reverse/ can use the exact same receiver[] breakdown.
    split_snapshot = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "flitt_transactions"
        ordering = ["-created_at"]
        verbose_name = "Flitt Transaction"
        verbose_name_plural = "Flitt Transactions"
        indexes = [
            models.Index(fields=["flow_type", "status"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["settlement_status"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - admin str
        return f"Flitt {self.flow_type} {self.flitt_order_id} ({self.status})"

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            self.STATUS_APPROVED,
            self.STATUS_DECLINED,
            self.STATUS_EXPIRED,
            self.STATUS_REVERSED,
            self.STATUS_REVERSED_PARTIALLY,
        }

    @property
    def is_successful(self) -> bool:
        return self.status == self.STATUS_APPROVED

    @property
    def restaurant_for_txn(self):
        """Resolve the restaurant this transaction belongs to regardless of flow."""
        if self.order_id:
            return self.order.restaurant  # type: ignore[union-attr]
        if self.reservation_id:
            return self.reservation.restaurant  # type: ignore[union-attr]
        if self.session_id:
            return self.session.restaurant  # type: ignore[union-attr]
        return None


class RestaurantDebit(TimeStampedModel):
    """
    Amount the platform is owed by a restaurant that couldn't be auto-collected.

    Primary use case: BOG refunds don't claw back the restaurant's 95 % share
    from the split — the money already landed in the restaurant's bank account.
    We write a ``RestaurantDebit`` row for the restaurant's portion of the
    refund and platform admin settles it off-platform (invoice, next payout
    netting, etc.).

    Flitt refunds use ``/api/reverse/`` with explicit ``receiver[]`` per-leg
    amounts, so no debit is created on that path.
    """

    SOURCE_BOG_REFUND = "bog_refund"
    SOURCE_MANUAL = "manual_adjustment"
    SOURCE_CHOICES = [
        (SOURCE_BOG_REFUND, "BOG refund claw-back"),
        (SOURCE_MANUAL, "Manual adjustment"),
    ]

    STATUS_OUTSTANDING = "outstanding"
    STATUS_SETTLED = "settled"
    STATUS_WRITTEN_OFF = "written_off"
    STATUS_CHOICES = [
        (STATUS_OUTSTANDING, "Outstanding"),
        (STATUS_SETTLED, "Settled"),
        (STATUS_WRITTEN_OFF, "Written off"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.PROTECT,
        related_name="platform_debits",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    currency = models.CharField(max_length=3, default="GEL")
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, db_index=True)

    source_bog_transaction = models.ForeignKey(
        BogTransaction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="restaurant_debits",
    )
    source_refund = models.ForeignKey(
        Refund,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="restaurant_debits",
    )

    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_OUTSTANDING,
        db_index=True,
    )
    settled_at = models.DateTimeField(null=True, blank=True)
    settled_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "restaurant_debits"
        ordering = ["-created_at"]
        verbose_name = "Restaurant Debit"
        verbose_name_plural = "Restaurant Debits"
        indexes = [
            models.Index(fields=["restaurant", "status"]),
            models.Index(fields=["source", "-created_at"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.restaurant} owes {self.amount} {self.currency} ({self.status})"
