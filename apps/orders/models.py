"""
Order models for restaurant order management.
"""

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


def _q(value):
    """Money quantizer: 0.01, half-up."""
    from decimal import ROUND_HALF_UP, Decimal

    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class OrderDiscount(TimeStampedModel):
    """
    One order-level discount. Several may coexist (a manual one and the
    platform loyalty tier); ``Order.discount_amount`` is the sum of these plus
    the item-level discounts. ``amount`` is frozen by ``calculate_totals``.
    """

    KIND_CHOICES = [
        ("manual", _("Manual")),
        ("loyalty_tier", _("Loyalty tier")),
        ("promo", _("Promotion")),
    ]
    MODE_CHOICES = [("percent", "Percent"), ("fixed", "Fixed amount")]

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="discounts")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="manual")
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default="percent")
    value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    reason = models.ForeignKey(
        "payments.DiscountReason", on_delete=models.SET_NULL, null=True, blank=True, related_name="order_discounts"
    )
    reason_text = models.CharField(max_length=200, blank=True, default="")
    applied_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="applied_discounts"
    )
    promotion = models.ForeignKey(
        "promotions.Promotion", on_delete=models.SET_NULL, null=True, blank=True, related_name="order_discounts"
    )

    class Meta:
        db_table = "order_discounts"
        ordering = ["created_at"]

    def __str__(self):
        unit = "%" if self.mode == "percent" else ""
        return f"{self.get_kind_display()} {self.value}{unit} (-{self.amount})"

    @property
    def label(self) -> str:
        if self.reason_id and self.reason:
            return self.reason.label
        return self.reason_text or self.get_kind_display()


class Order(TimeStampedModel):
    """
    Customer order containing multiple items.
    """

    STATUS_CHOICES = [
        # Created but awaiting a successful payment callback before going to the kitchen.
        ("pending_payment", _("Pending Payment")),
        ("pending", _("Pending")),
        ("confirmed", _("Confirmed")),
        ("preparing", _("Preparing")),
        ("ready", _("Ready")),
        ("served", _("Served")),
        ("completed", _("Completed")),
        ("cancelled", _("Cancelled")),
    ]

    ORDER_TYPE_CHOICES = [
        ("dine_in", _("Dine In")),
        ("takeaway", _("Takeaway")),
        ("delivery", _("Delivery")),
    ]

    SOURCE_CHOICES = [
        ("web", _("Website")),
        ("qr", _("QR table")),
        ("pos", _("POS")),
        ("glovo", _("Glovo")),
        ("wolt", _("Wolt")),
        ("bolt_food", _("Bolt Food")),
    ]

    # Order identification
    order_number = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text=_("Human-readable order number (e.g., ORD-001)"),
    )

    # Relationships
    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="orders",
    )
    table = models.ForeignKey(
        "tables.Table",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    table_session = models.ForeignKey(
        "tables.TableSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    session_guest = models.ForeignKey(
        "tables.TableSessionGuest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        help_text=_("The guest at the table who placed this order"),
    )
    reservation = models.ForeignKey(
        "reservations.Reservation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
        help_text=_("Optional — set when this order was placed as part of a reservation."),
    )
    customer = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )

    # Order details
    order_type = models.CharField(
        max_length=20,
        choices=ORDER_TYPE_CHOICES,
        default="dine_in",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    # Pricing
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    tax_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    service_charge = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    wallet_applied = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text=(
            "Wallet balance the customer spent on this order. Discount-like; "
            "calculate_totals subtracts it. Actual debit happens at payment success."
        ),
    )
    tip_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text=_("Optional gratuity paid on top of subtotal/tax/service."),
    )
    tip_distribution = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Phase-3 tip allocation map. {user_id_or_pool: decimal_amount}. "
            "Unassigned tips accrue to the server FK or the restaurant pool."
        ),
    )
    server = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="served_orders",
        help_text=_("Staff member credited with serving this order (for tip distribution)."),
    )
    total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    # Where the order came from; delivery platforms keep their own id and payload.
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="web", db_index=True)
    external_id = models.CharField(max_length=100, blank=True, default="")
    platform_data = models.JSONField(default=dict, blank=True)

    # Fiscal snapshot at the time totals were computed (see apps.fiscal.vat).
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    prices_include_vat = models.BooleanField(default=False)

    # Customer info (for guest orders or delivery)
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    customer_email = models.EmailField(blank=True)
    customer_notes = models.TextField(
        blank=True,
        help_text=_("Special requests or notes from customer"),
    )

    # Delivery info (for delivery orders)
    delivery_address = models.TextField(blank=True)

    # Timing
    estimated_ready_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    prepared_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)

    # Staff
    handled_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handled_orders",
        help_text=_("Staff member who handled this order"),
    )

    class Meta:
        db_table = "orders"
        ordering = ["-created_at"]
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
        indexes = [
            models.Index(fields=["restaurant", "status"]),
            models.Index(fields=["restaurant", "created_at"]),
            models.Index(fields=["order_number"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant", "source", "external_id"],
                condition=~models.Q(external_id=""),
                name="order_external_id_unique_per_source",
            ),
        ]

    def __str__(self):
        return f"Order {self.order_number}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            # Retry a handful of times in case the daily counter raced against
            # another order.save() across workers — rare but real under parallel
            # gunicorn writes.
            from django.db import IntegrityError, transaction

            attempts = 0
            while True:
                attempts += 1
                self.order_number = self._generate_order_number()
                try:
                    with transaction.atomic():
                        return super().save(*args, **kwargs)
                except IntegrityError:
                    if attempts >= 5:
                        raise
                    # Let the counter reread and retry.
        super().save(*args, **kwargs)

    def _generate_order_number(self) -> str:
        """
        Generate a globally-unique ``ORD-YYMMDD-NNNN`` number for today.

        The uniqueness constraint on ``order_number`` is global (not per-tenant),
        so the daily counter must be too. Prior versions scoped the count by
        ``restaurant`` which produced collisions like
        ``UniqueViolation: Key (order_number)=(ORD-260420-0001) already exists``
        whenever two different restaurants created their first order of the
        day in the same second.
        """
        from django.utils import timezone

        today = timezone.localdate()
        prefix = today.strftime("%y%m%d")
        count = Order.objects.filter(created_at__date=today).count()
        return f"ORD-{prefix}-{count + 1:04d}"

    def calculate_totals(self):
        """
        Recalculate order totals from the live (non-voided) items.

        subtotal        = gross of live items
        discount_amount = item-level discounts/comps + order-level discount rows
                          (percent rows are re-derived on the post-item-discount
                          base; fixed rows are capped at what is left)
        tax / service   = percentages of the net (subtotal - discounts)
        total           = net + tax + service + tip - wallet
        Everything is quantized to 0.01 so the receipt adds up.
        """
        from decimal import Decimal

        from django.db.models import Sum

        q = _q
        live = self.items.exclude(status="cancelled")
        agg = live.aggregate(gross=Sum("total_price"), item_discounts=Sum("discount_amount"))
        gross = q(agg["gross"] or Decimal("0"))
        item_discounts = min(q(agg["item_discounts"] or Decimal("0")), gross)
        base = gross - item_discounts

        order_discounts = Decimal("0")
        if self.pk:
            for d in self.discounts.all().order_by("created_at"):
                room = base - order_discounts
                if d.mode == "percent":
                    amount = q(base * d.value / Decimal("100"))
                else:
                    amount = q(d.value)
                amount = max(min(amount, room), Decimal("0"))
                if d.amount != amount:
                    d.amount = amount
                    d.save(update_fields=["amount", "updated_at"])
                order_discounts += amount

        self.subtotal = gross
        self.discount_amount = item_discounts + order_discounts
        net = gross - self.discount_amount

        from apps.fiscal.vat import add_vat, split_gross, tax_context

        service_rate = self.restaurant.service_charge if self.restaurant else Decimal("0")
        self.service_charge = q(net * (service_rate or Decimal("0")) / Decimal("100"))
        ctx = tax_context(self.restaurant) if self.restaurant_id else None
        rate = ctx.rate if ctx else Decimal("0")
        inclusive = bool(ctx and ctx.inclusive)
        self.vat_rate = rate
        self.prices_include_vat = inclusive
        if inclusive:
            # Georgian menus are gross: VAT is part of the price (informational),
            # nothing is added on top.
            self.tax_amount = split_gross(net + self.service_charge, rate)[1]
            tax_added = Decimal("0")
        else:
            # Legacy / exclusive: tax on the net, service charge untaxed.
            self.tax_amount = add_vat(net, rate)[1]
            tax_added = self.tax_amount

        # Tip is customer-set; wallet is treated like a discount but kept
        # separate so refunds can identify wallet-funded amounts.
        total = net + tax_added + self.service_charge + q(self.tip_amount or 0) - q(self.wallet_applied or 0)
        self.total = max(q(total), Decimal("0"))

        self.save(
            update_fields=[
                "subtotal",
                "discount_amount",
                "tax_amount",
                "service_charge",
                "vat_rate",
                "prices_include_vat",
                "total",
                "updated_at",
            ]
        )

    @property
    def live_items(self):
        return self.items.exclude(status="cancelled")

    def confirm(self, estimated_minutes: int = None):
        """Confirm the order."""
        from django.utils import timezone

        self.status = "confirmed"
        self.confirmed_at = timezone.now()

        if estimated_minutes:
            self.estimated_ready_at = timezone.now() + timezone.timedelta(minutes=estimated_minutes)

        self.save(update_fields=["status", "confirmed_at", "estimated_ready_at", "updated_at"])

    def cancel(self, reason: str = ""):
        """Cancel the order."""
        from django.utils import timezone

        self.status = "cancelled"
        self.cancelled_at = timezone.now()
        self.cancellation_reason = reason
        self.save(update_fields=["status", "cancelled_at", "cancellation_reason", "updated_at"])

    def complete(self):
        """Mark order as completed."""
        from django.utils import timezone

        self.status = "completed"
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at", "updated_at"])

    @property
    def is_editable(self) -> bool:
        """Check if order can still be modified."""
        return self.status in ["pending", "confirmed"]

    @property
    def can_cancel(self) -> bool:
        """Check if order can be cancelled."""
        return self.status not in ["completed", "cancelled", "served"]


class OrderItem(TimeStampedModel):
    """
    Individual item within an order.
    """

    STATUS_CHOICES = [
        ("pending", _("Pending")),
        ("preparing", _("Preparing")),
        ("ready", _("Ready")),
        ("served", _("Served")),
        ("cancelled", _("Cancelled")),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    menu_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.SET_NULL,
        null=True,
        related_name="order_items",
    )

    # Snapshot of item details at time of order (in case menu changes)
    item_name = models.CharField(max_length=200)
    item_description = models.TextField(blank=True)
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    quantity = models.PositiveSmallIntegerField(default=1)
    total_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )

    # Item status (for kitchen tracking)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    preparation_station = models.CharField(
        max_length=20,
        choices=[
            ("kitchen", _("Kitchen")),
            ("bar", _("Bar")),
            ("both", _("Both")),
        ],
        default="kitchen",
    )

    # Customer customization
    special_instructions = models.TextField(blank=True)

    # Discounts / comps (item level). A comp is a 100% discount flagged so
    # reports can tell "on the house" from "10% off".
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    is_comped = models.BooleanField(default=False)
    discount_reason = models.ForeignKey(
        "payments.DiscountReason", on_delete=models.SET_NULL, null=True, blank=True, related_name="item_discounts"
    )
    discount_reason_text = models.CharField(max_length=200, blank=True, default="")
    promotion = models.ForeignKey(
        "promotions.Promotion", on_delete=models.SET_NULL, null=True, blank=True, related_name="order_items"
    )
    discounted_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="discounted_items"
    )

    # Voids: status="cancelled" plus who / why / when. ``was_sent_to_kitchen``
    # separates a real void (food may have been cooked) from an item removed
    # before the order ever reached the kitchen.
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="voided_items"
    )
    void_reason = models.ForeignKey(
        "payments.DiscountReason", on_delete=models.SET_NULL, null=True, blank=True, related_name="item_voids"
    )
    void_reason_text = models.CharField(max_length=200, blank=True, default="")
    was_sent_to_kitchen = models.BooleanField(default=False)

    class Meta:
        db_table = "order_items"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.quantity}x {self.item_name}"

    @property
    def net_price(self):
        """What the customer pays for this line after item-level discounts."""
        from decimal import Decimal

        return max((self.total_price or Decimal("0")) - (self.discount_amount or Decimal("0")), Decimal("0"))

    @property
    def is_voided(self) -> bool:
        return self.status == "cancelled"

    def save(self, *args, **kwargs):
        # Calculate total price
        if self.unit_price:
            modifiers_total = sum(m.price_adjustment for m in self.modifiers.all())
            self.total_price = (self.unit_price + modifiers_total) * self.quantity
        super().save(*args, **kwargs)

    def recalculate_total(self):
        """Recalculate total price including modifiers."""
        modifiers_total = sum(m.price_adjustment for m in self.modifiers.all())
        self.total_price = (self.unit_price + modifiers_total) * self.quantity
        self.save(update_fields=["total_price", "updated_at"])

        # Update order totals
        self.order.calculate_totals()


class OrderItemModifier(TimeStampedModel):
    """
    Modifier applied to an order item.
    """

    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="modifiers",
    )
    modifier = models.ForeignKey(
        "menu.Modifier",
        on_delete=models.SET_NULL,
        null=True,
        related_name="order_item_modifiers",
    )

    # Snapshot at time of order
    modifier_name = models.CharField(max_length=100)
    price_adjustment = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    class Meta:
        db_table = "order_item_modifiers"

    def __str__(self):
        return f"{self.modifier_name} on {self.order_item}"


class OrderStatusHistory(TimeStampedModel):
    """
    Track order status changes for audit trail.
    """

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="status_history",
    )
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "order_status_history"
        ordering = ["-created_at"]
        verbose_name_plural = _("Order status histories")

    def __str__(self):
        return f"{self.order} {self.from_status} → {self.to_status}"
