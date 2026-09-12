"""
Warehouse models.

Quantities are stored in the stock item's *base unit* (grams, millilitres or
pieces) as 4dp decimals; the ledger is append-only and the per-item counters
(``on_hand_qty`` / ``reserved_qty``) are caches maintained under row locks by
apps.inventory.services -- ``rebuild_stock_cache`` recomputes them from the
ledger if they ever drift.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.inventory.exceptions import UnitMismatch

QTY = Decimal("0.0001")
COST = Decimal("0.000001")
MONEY = Decimal("0.01")


def q4(value) -> Decimal:
    return Decimal(value).quantize(QTY, rounding=ROUND_HALF_UP)


def money(value) -> Decimal:
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def fmt_qty(value, unit=None) -> str:
    """Human quantity: '1.5 kg', '240 g' -- no trailing zeros."""
    qty = Decimal(value).normalize()
    text = f"{qty:f}" if qty == qty.to_integral() else f"{qty:.4f}".rstrip("0").rstrip(".")
    return f"{text} {unit.code}" if unit is not None else text


class UnitOfMeasure(models.Model):
    """Global unit list (seeded). Conversions only happen within a dimension."""

    DIMENSION_CHOICES = [("mass", "Mass"), ("volume", "Volume"), ("count", "Count")]

    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=50)
    dimension = models.CharField(max_length=10, choices=DIMENSION_CHOICES)
    factor_to_base = models.DecimalField(max_digits=16, decimal_places=6, default=1)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "inventory_units"
        ordering = ["dimension", "display_order", "code"]
        verbose_name = _("Unit")
        verbose_name_plural = _("Units")

    def __str__(self):
        return self.code

    @staticmethod
    def convert(qty, from_unit, to_unit) -> Decimal:
        if from_unit.dimension != to_unit.dimension:
            raise UnitMismatch(f"Cannot convert {from_unit.code} to {to_unit.code}.")
        return q4(Decimal(qty) * from_unit.factor_to_base / to_unit.factor_to_base)

    def to_base(self, qty) -> Decimal:
        return q4(Decimal(qty) * self.factor_to_base)


class StockItem(TimeStampedModel):
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="stock_items")
    name = models.CharField(max_length=150)
    sku = models.CharField(max_length=50, blank=True, default="")
    category = models.CharField(max_length=50, blank=True, default="", help_text="Free text, e.g. Dairy, Meat, Dry.")
    base_unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Unit recipes and counts are kept in (g, ml, pcs).",
    )
    purchase_unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
        help_text="Unit you buy it in (e.g. kg). Same dimension as the base unit.",
    )
    purchase_pack_qty = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Size of one purchase pack in the purchase unit (a 10 kg bag = 10).",
    )
    min_level = models.DecimalField(
        max_digits=14, decimal_places=4, default=0, help_text="Alert when available stock drops to this (base unit)."
    )
    par_level = models.DecimalField(
        max_digits=14, decimal_places=4, default=0, help_text="Target stock level the buy list tops up to (base unit)."
    )
    expiry_warning_days = models.PositiveSmallIntegerField(default=3)
    supplier_name = models.CharField(max_length=150, blank=True, default="")
    default_unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Cost per base unit used when no lot cost is known.",
    )
    is_active = models.BooleanField(default=True)

    # Caches (base units). Only written inside services under select_for_update.
    on_hand_qty = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    reserved_qty = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    class Meta:
        db_table = "inventory_stock_items"
        ordering = ["name"]
        unique_together = [("restaurant", "name")]
        indexes = [models.Index(fields=["restaurant", "is_active"])]
        verbose_name = _("Stock item")
        verbose_name_plural = _("Stock items")

    def __str__(self):
        return self.name

    @property
    def available_qty(self) -> Decimal:
        return self.on_hand_qty - self.reserved_qty

    @property
    def level(self) -> str:
        """out / low / ok -- used by admin badges and reports."""
        available = self.available_qty
        if available <= 0:
            return "out"
        if available <= self.min_level:
            return "low"
        return "ok"

    def clean(self):
        if self.purchase_unit_id and self.base_unit_id and self.purchase_unit.dimension != self.base_unit.dimension:
            raise ValidationError({"purchase_unit": "Purchase unit must have the same dimension as the base unit."})
        if self.purchase_unit_id and not self.purchase_pack_qty:
            self.purchase_pack_qty = Decimal("1")

    def current_unit_cost(self) -> Decimal | None:
        """Remaining-weighted average of open lots, else the last lot, else the default."""
        agg = self.lots.filter(remaining_qty__gt=0).aggregate(
            qty=models.Sum("remaining_qty"),
            value=models.Sum(models.F("remaining_qty") * models.F("unit_cost")),
        )
        if agg["qty"]:
            return (Decimal(agg["value"]) / Decimal(agg["qty"])).quantize(COST)
        last = self.lots.order_by("-received_at", "-pk").values_list("unit_cost", flat=True).first()
        if last is not None:
            return last
        return self.default_unit_cost


class StockLot(TimeStampedModel):
    stock_item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name="lots")
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="stock_lots")
    received_qty = models.DecimalField(max_digits=14, decimal_places=4)
    remaining_qty = models.DecimalField(max_digits=14, decimal_places=4)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=6, default=0, help_text="Per base unit.")
    total_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    expiry_date = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    supplier_name = models.CharField(max_length=150, blank=True, default="")
    reference = models.CharField(max_length=100, blank=True, default="", help_text="Invoice / delivery note number.")
    received_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="received_stock_lots"
    )
    is_expired_out = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "inventory_stock_lots"
        ordering = ["expiry_date", "received_at"]
        indexes = [
            models.Index(fields=["stock_item", "expiry_date", "received_at"]),
            models.Index(fields=["restaurant", "expiry_date"]),
        ]
        constraints = [models.CheckConstraint(check=Q(remaining_qty__gte=0), name="inventory_lot_remaining_nonneg")]
        verbose_name = _("Stock lot")
        verbose_name_plural = _("Stock lots (receiving)")

    def __str__(self):
        unit = self.stock_item.base_unit
        return f"{self.stock_item} · {fmt_qty(self.remaining_qty)}/{fmt_qty(self.received_qty, unit)} · {self.expiry_date or 'no expiry'}"

    @property
    def days_to_expiry(self):
        if not self.expiry_date:
            return None
        return (self.expiry_date - timezone.localdate()).days


class StockMovement(TimeStampedModel):
    """Append-only ledger. ``quantity`` is a signed delta in base units."""

    KIND_CHOICES = [
        ("receive", "Received"),
        ("consume_sale", "Consumed by order"),
        ("restore_sale", "Restored (order cancelled)"),
        ("waste", "Waste / write-off"),
        ("employee_meal", "Employee meal"),
        ("adjustment", "Adjustment / count"),
    ]
    REASON_CHOICES = [
        ("", "—"),
        ("spoilage", "Spoiled"),
        ("expired", "Expired"),
        ("burnt", "Kitchen mistake"),
        ("count", "Stock count"),
        ("correction", "Correction"),
        ("other", "Other"),
    ]

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="stock_movements")
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="movements")
    lot = models.ForeignKey(StockLot, on_delete=models.SET_NULL, null=True, blank=True, related_name="movements")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, blank=True, default="")
    quantity = models.DecimalField(max_digits=14, decimal_places=4)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    total_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_movements"
    )
    order_item = models.ForeignKey(
        "orders.OrderItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_movements"
    )
    waste_entry = models.ForeignKey(
        "inventory.WasteEntry", on_delete=models.SET_NULL, null=True, blank=True, related_name="movements"
    )
    employee_meal = models.ForeignKey(
        "inventory.EmployeeMeal", on_delete=models.SET_NULL, null=True, blank=True, related_name="movements"
    )
    adjustment = models.ForeignKey(
        "inventory.StockAdjustment", on_delete=models.SET_NULL, null=True, blank=True, related_name="movements"
    )
    staff_user = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_movements"
    )
    note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "inventory_stock_movements"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["restaurant", "created_at"]),
            models.Index(fields=["stock_item", "created_at"]),
            models.Index(fields=["order"]),
        ]
        verbose_name = _("Stock movement")
        verbose_name_plural = _("Stock movements")

    def __str__(self):
        return f"{self.get_kind_display()} {self.quantity} {self.stock_item}"


class OrderStockReservation(TimeStampedModel):
    """
    Ingredients held for one order between "placed" and "accepted".

    ``status`` is the idempotency key: every transition is checked under a row
    lock, so webhook retries and double taps are harmless.
    """

    STATUS_RESERVED = "reserved"
    STATUS_CONSUMED = "consumed"
    STATUS_RELEASED = "released"
    STATUS_RESTORED = "restored"
    STATUS_CHOICES = [
        (STATUS_RESERVED, "Reserved"),
        (STATUS_CONSUMED, "Consumed"),
        (STATUS_RELEASED, "Released"),
        (STATUS_RESTORED, "Restored"),
    ]

    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="stock_reservation")
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="stock_reservations")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_RESERVED)
    strict = models.BooleanField(default=True, help_text="False when a paid order was forced through short stock.")
    consumed_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "inventory_order_reservations"
        indexes = [models.Index(fields=["restaurant", "status"])]

    def __str__(self):
        return f"{self.order_id} · {self.status}"


class OrderStockReservationLine(models.Model):
    reservation = models.ForeignKey(OrderStockReservation, on_delete=models.CASCADE, related_name="lines")
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="reservation_lines")
    order_item = models.ForeignKey(
        "orders.OrderItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_reservation_lines"
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=4)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "inventory_order_reservation_lines"
        indexes = [models.Index(fields=["stock_item", "is_active"])]


class RecipeLine(TimeStampedModel):
    """One ingredient of a dish (menu item) or of a modifier, per portion."""

    menu_item = models.ForeignKey(
        "menu.MenuItem", on_delete=models.CASCADE, null=True, blank=True, related_name="recipe_lines"
    )
    modifier = models.ForeignKey(
        "menu.Modifier", on_delete=models.CASCADE, null=True, blank=True, related_name="recipe_lines"
    )
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="recipe_lines")
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    note = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "inventory_recipe_lines"
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(menu_item__isnull=False, modifier__isnull=True)
                    | Q(menu_item__isnull=True, modifier__isnull=False)
                ),
                name="inventory_recipe_line_one_target",
            ),
            models.UniqueConstraint(
                fields=["menu_item", "stock_item"],
                condition=Q(menu_item__isnull=False),
                name="inventory_recipe_item_uniq",
            ),
            models.UniqueConstraint(
                fields=["modifier", "stock_item"], condition=Q(modifier__isnull=False), name="inventory_recipe_mod_uniq"
            ),
        ]
        verbose_name = _("Recipe line")
        verbose_name_plural = _("Recipe lines")

    def __str__(self):
        return f"{fmt_qty(self.quantity, self.unit)} {self.stock_item}"

    @property
    def target_restaurant_id(self):
        if self.menu_item_id:
            return self.menu_item.restaurant_id
        if self.modifier_id:
            return self.modifier.group.restaurant_id
        return None

    def clean(self):
        if bool(self.menu_item_id) == bool(self.modifier_id):
            raise ValidationError("A recipe line belongs to exactly one dish or one modifier.")
        if self.stock_item_id and self.unit_id and self.unit.dimension != self.stock_item.base_unit.dimension:
            raise ValidationError(
                {
                    "unit": f"{self.stock_item.name} is tracked in {self.stock_item.base_unit.code}; pick a matching unit."
                }
            )
        if (
            self.stock_item_id
            and self.target_restaurant_id
            and self.stock_item.restaurant_id != self.target_restaurant_id
        ):
            raise ValidationError({"stock_item": "Stock item belongs to another restaurant."})
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({"quantity": "Quantity must be positive."})

    @property
    def qty_base(self) -> Decimal:
        return self.unit.to_base(self.quantity)


class WasteEntry(TimeStampedModel):
    REASON_CHOICES = [
        ("spoilage", "Spoiled / went bad"),
        ("expired", "Expired"),
        ("burnt", "Kitchen mistake (burnt, dropped, wrong dish)"),
        ("other", "Other"),
    ]

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="waste_entries")
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="waste_entries")
    lot = models.ForeignKey(
        StockLot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waste_entries",
        help_text="Optional: the exact lot that was thrown away. Otherwise the oldest stock is written off first.",
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default="spoilage")
    note = models.TextField(blank=True, default="")
    reported_by = models.ForeignKey(
        "staff.StaffMember", on_delete=models.SET_NULL, null=True, blank=True, related_name="waste_entries"
    )
    occurred_on = models.DateField(default=timezone.localdate)
    total_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        db_table = "inventory_waste_entries"
        ordering = ["-occurred_on", "-created_at"]
        verbose_name = _("Waste / spoilage")
        verbose_name_plural = _("Waste / spoilage / mistakes")
        indexes = [models.Index(fields=["restaurant", "occurred_on"])]

    def __str__(self):
        return f"{self.get_reason_display()}: {fmt_qty(self.quantity, self.unit)} {self.stock_item}"

    def clean(self):
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({"quantity": "Quantity must be positive."})
        if self.stock_item_id and self.unit_id and self.unit.dimension != self.stock_item.base_unit.dimension:
            raise ValidationError({"unit": f"{self.stock_item.name} is tracked in {self.stock_item.base_unit.code}."})
        if self.lot_id and self.stock_item_id and self.lot.stock_item_id != self.stock_item_id:
            raise ValidationError({"lot": "That lot belongs to a different stock item."})


class EmployeeMeal(TimeStampedModel):
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="employee_meals")
    staff_member = models.ForeignKey(
        "staff.StaffMember", on_delete=models.PROTECT, related_name="meals", help_text="Who ate."
    )
    meal_date = models.DateField(default=timezone.localdate)
    menu_item = models.ForeignKey(
        "menu.MenuItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="employee_meals"
    )
    stock_item = models.ForeignKey(
        StockItem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="employee_meals",
        help_text="For raw ingredients eaten as-is (fruit, bread...).",
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    note = models.CharField(max_length=200, blank=True, default="")
    recorded_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="recorded_employee_meals"
    )
    total_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        db_table = "inventory_employee_meals"
        ordering = ["-meal_date", "-created_at"]
        verbose_name = _("Employee meal")
        verbose_name_plural = _("Employee meals")
        indexes = [models.Index(fields=["restaurant", "meal_date"])]

    def __str__(self):
        what = self.menu_item or self.stock_item
        return f"{self.staff_member} ate {fmt_qty(self.quantity)} × {what} on {self.meal_date}"

    def clean(self):
        if bool(self.menu_item_id) == bool(self.stock_item_id):
            raise ValidationError("Pick either a dish or a raw stock item.")
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({"quantity": "Quantity must be positive."})
        if self.stock_item_id:
            if not self.unit_id:
                raise ValidationError({"unit": "Unit is required for a raw stock item."})
            if self.unit.dimension != self.stock_item.base_unit.dimension:
                raise ValidationError(
                    {"unit": f"{self.stock_item.name} is tracked in {self.stock_item.base_unit.code}."}
                )
        if self.menu_item_id and self.restaurant_id and self.menu_item.restaurant_id != self.restaurant_id:
            raise ValidationError({"menu_item": "Dish belongs to another restaurant."})


class StockAdjustment(TimeStampedModel):
    MODE_CHOICES = [("count", "Stock count (I counted this much)"), ("delta", "Correction (+/- this much)")]
    REASON_CHOICES = [("count", "Stock count"), ("correction", "Correction"), ("other", "Other")]

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="stock_adjustments")
    stock_item = models.ForeignKey(StockItem, on_delete=models.PROTECT, related_name="adjustments")
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default="count")
    quantity = models.DecimalField(max_digits=12, decimal_places=4, help_text="Counted amount, or the +/- correction.")
    unit = models.ForeignKey(UnitOfMeasure, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default="count")
    note = models.TextField(blank=True, default="")
    made_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_adjustments"
    )
    delta_base = models.DecimalField(
        max_digits=14, decimal_places=4, default=0, help_text="Resulting change in base units (filled on save)."
    )

    class Meta:
        db_table = "inventory_stock_adjustments"
        ordering = ["-created_at"]
        verbose_name = _("Stock count / adjustment")
        verbose_name_plural = _("Stock counts & adjustments")

    def __str__(self):
        return f"{self.get_mode_display()} {fmt_qty(self.quantity, self.unit)} {self.stock_item}"

    def clean(self):
        if self.mode == "count" and self.quantity is not None and self.quantity < 0:
            raise ValidationError({"quantity": "A count cannot be negative."})
        if self.stock_item_id and self.unit_id and self.unit.dimension != self.stock_item.base_unit.dimension:
            raise ValidationError({"unit": f"{self.stock_item.name} is tracked in {self.stock_item.base_unit.code}."})


class RestaurantDeliveryPlatform(TimeStampedModel):
    PLATFORM_CHOICES = [("glovo", "Glovo"), ("wolt", "Wolt"), ("bolt_food", "Bolt Food")]

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="delivery_platforms")
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    is_enabled = models.BooleanField(default=True)
    store_external_id = models.CharField(max_length=100, blank=True, default="", help_text="Your store id there.")
    credentials_encrypted = models.TextField(blank=True, default="")
    webhook_token = models.CharField(max_length=64, blank=True, default="", db_index=True)
    menu_token = models.CharField(
        max_length=64, blank=True, default="", help_text="Unguessable part of the public menu URL."
    )
    auto_accept = models.BooleanField(default=True, help_text="Platform orders go straight to the kitchen.")
    prep_time_minutes = models.PositiveSmallIntegerField(default=20)
    last_menu_sync_at = models.DateTimeField(null=True, blank=True)
    last_menu_sync_status = models.CharField(max_length=20, blank=True, default="")
    sandbox = models.BooleanField(default=True, help_text="Use the platform's stage environment.")
    store_paused_until = models.DateTimeField(
        null=True, blank=True, help_text="We asked the platform to hide the store until then."
    )

    class Meta:
        db_table = "inventory_delivery_platforms"
        unique_together = [("restaurant", "platform")]
        ordering = ["platform"]
        verbose_name = _("Delivery platform")
        verbose_name_plural = _("Delivery platforms (Glovo / Wolt / Bolt Food)")

    def __str__(self):
        return self.get_platform_display()

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


class InventoryAlert(TimeStampedModel):
    KIND_CHOICES = [
        ("out_of_stock", "Dish sold out"),
        ("back_in_stock", "Dish back in stock"),
        ("low_stock", "Low stock"),
        ("expiring_soon", "Expiring soon"),
        ("expired", "Expired"),
        ("negative_stock", "Negative stock"),
        ("buy_list", "Buy list ready"),
    ]
    STATUS_OPEN = "open"
    STATUS_DONE = "done"
    STATUS_CHOICES = [(STATUS_OPEN, "Open"), (STATUS_DONE, "Done")]

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="inventory_alerts")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN)
    stock_item = models.ForeignKey(StockItem, on_delete=models.CASCADE, null=True, blank=True, related_name="alerts")
    menu_item = models.ForeignKey(
        "menu.MenuItem", on_delete=models.CASCADE, null=True, blank=True, related_name="inventory_alerts"
    )
    modifier = models.ForeignKey(
        "menu.Modifier", on_delete=models.CASCADE, null=True, blank=True, related_name="inventory_alerts"
    )
    lot = models.ForeignKey(StockLot, on_delete=models.CASCADE, null=True, blank=True, related_name="alerts")
    message = models.CharField(max_length=300)
    payload = models.JSONField(default=dict, blank=True)
    dedupe_key = models.CharField(max_length=200)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="resolved_inventory_alerts"
    )
    resolved_reason = models.CharField(max_length=20, blank=True, default="", help_text="auto / manual")

    class Meta:
        db_table = "inventory_alerts"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["restaurant", "status", "created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant", "dedupe_key"], condition=Q(status="open"), name="inventory_alert_one_open_per_key"
            )
        ]
        verbose_name = _("Alert")
        verbose_name_plural = _("Alerts")

    def __str__(self):
        return self.message

    @property
    def is_open(self):
        return self.status == self.STATUS_OPEN

    def resolve(self, *, by=None, reason="manual"):
        if self.status == self.STATUS_DONE:
            return
        self.status = self.STATUS_DONE
        self.resolved_at = timezone.now()
        self.resolved_by = by
        self.resolved_reason = reason
        self.save(update_fields=["status", "resolved_at", "resolved_by", "resolved_reason", "updated_at"])


class InventoryAlertPlatformTask(TimeStampedModel):
    """'Disable Margherita on Glovo' -- one checklist row per enabled platform."""

    ACTION_CHOICES = [("disable", "Disable"), ("enable", "Enable")]
    ADAPTER_CHOICES = [("manual", "Manual"), ("sent", "Sent via API"), ("failed", "API failed")]

    alert = models.ForeignKey(InventoryAlert, on_delete=models.CASCADE, related_name="platform_tasks")
    platform = models.ForeignKey(RestaurantDeliveryPlatform, on_delete=models.CASCADE, related_name="tasks")
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    is_done = models.BooleanField(default=False)
    done_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="done_platform_tasks"
    )
    done_at = models.DateTimeField(null=True, blank=True)
    adapter_status = models.CharField(max_length=10, choices=ADAPTER_CHOICES, default="manual")
    adapter_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "inventory_alert_platform_tasks"
        unique_together = [("alert", "platform")]
        ordering = ["platform__platform"]

    def __str__(self):
        return f"{self.get_action_display()} on {self.platform}"

    def toggle(self, user, done: bool):
        self.is_done = done
        self.done_by = user if done else None
        self.done_at = timezone.now() if done else None
        self.save(update_fields=["is_done", "done_by", "done_at", "updated_at"])


class WarehouseOverview(InventoryAlert):
    """Proxy so the tenant admin can hang the overview page on its own sidebar entry."""

    class Meta:
        proxy = True
        verbose_name = _("Warehouse overview")
        verbose_name_plural = _("Warehouse overview")
