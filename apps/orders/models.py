"""
Order models for restaurant order management.
"""

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class Order(TimeStampedModel):
    """
    Customer order containing multiple items.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("confirmed", "Confirmed"),
        ("preparing", "Preparing"),
        ("ready", "Ready"),
        ("served", "Served"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    ORDER_TYPE_CHOICES = [
        ("dine_in", "Dine In"),
        ("takeaway", "Takeaway"),
        ("delivery", "Delivery"),
    ]

    # Order identification
    order_number = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text="Human-readable order number (e.g., ORD-001)",
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
    total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )

    # Customer info (for guest orders or delivery)
    customer_name = models.CharField(max_length=200, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    customer_email = models.EmailField(blank=True)
    customer_notes = models.TextField(
        blank=True,
        help_text="Special requests or notes from customer",
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
        help_text="Staff member who handled this order",
    )

    class Meta:
        db_table = "orders"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["restaurant", "status"]),
            models.Index(fields=["restaurant", "created_at"]),
            models.Index(fields=["order_number"]),
        ]

    def __str__(self):
        return f"Order {self.order_number}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

    def _generate_order_number(self) -> str:
        """Generate a unique order number."""
        from django.utils import timezone

        today = timezone.localdate()
        prefix = today.strftime("%y%m%d")

        # Get count of orders today for this restaurant
        count = Order.objects.filter(
            restaurant=self.restaurant,
            created_at__date=today,
        ).count()

        return f"ORD-{prefix}-{count + 1:04d}"

    def calculate_totals(self):
        """Recalculate order totals from items."""
        from django.db.models import Sum

        # Calculate subtotal from items
        items_total = self.items.aggregate(total=Sum("total_price"))["total"] or 0
        self.subtotal = items_total

        # Apply tax and service charge from restaurant settings
        if self.restaurant:
            self.tax_amount = self.subtotal * (self.restaurant.tax_rate / 100)
            self.service_charge = self.subtotal * (self.restaurant.service_charge / 100)

        # Calculate total
        self.total = self.subtotal + self.tax_amount + self.service_charge - self.discount_amount

        self.save(
            update_fields=[
                "subtotal",
                "tax_amount",
                "service_charge",
                "total",
                "updated_at",
            ]
        )

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
        ("pending", "Pending"),
        ("preparing", "Preparing"),
        ("ready", "Ready"),
        ("served", "Served"),
        ("cancelled", "Cancelled"),
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
            ("kitchen", "Kitchen"),
            ("bar", "Bar"),
            ("both", "Both"),
        ],
        default="kitchen",
    )

    # Customer customization
    special_instructions = models.TextField(blank=True)

    class Meta:
        db_table = "order_items"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.quantity}x {self.item_name}"

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
        verbose_name_plural = "Order status histories"

    def __str__(self):
        return f"{self.order} {self.from_status} → {self.to_status}"
