"""
Menu schedules (breakfast / lunch windows), promotions (happy hour, promo
codes) and combo components. Availability and discount maths live in
``availability.py`` / ``services.py``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

WEEKDAYS = [
    (0, _("Monday")),
    (1, _("Tuesday")),
    (2, _("Wednesday")),
    (3, _("Thursday")),
    (4, _("Friday")),
    (5, _("Saturday")),
    (6, _("Sunday")),
]
CHANNELS = [("web", _("Website")), ("qr", _("QR table")), ("pos", _("POS"))]


class MenuSchedule(TimeStampedModel):
    """When a category / dish / happy hour is on: weekdays and a time window (may cross midnight)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="menu_schedules")
    name = models.CharField(max_length=100, help_text=_("E.g. 'Breakfast', 'Lunch', 'Happy hour'."))
    weekdays = models.JSONField(default=list, blank=True, help_text=_("0 = Monday … 6 = Sunday. Empty = every day."))
    start_time = models.TimeField()
    end_time = models.TimeField(help_text=_("Earlier than the start = the window runs past midnight."))
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "menu_schedules"
        ordering = ["start_time", "name"]
        verbose_name = _("Menu schedule")
        verbose_name_plural = _("Menu schedules")

    def __str__(self):
        return f"{self.name} {self.start_time:%H:%M}–{self.end_time:%H:%M}"

    def matches(self, local_dt) -> bool:
        """True when ``local_dt`` (aware, restaurant tz) falls inside the window."""
        if not self.is_active:
            return False
        days = [int(d) for d in (self.weekdays or [])]
        t = local_dt.time()
        if self.start_time <= self.end_time:
            day_ok = not days or local_dt.weekday() in days
            return day_ok and self.start_time <= t < self.end_time
        # overnight: the part after midnight belongs to the previous weekday
        if t >= self.start_time:
            return not days or local_dt.weekday() in days
        prev = (local_dt.weekday() - 1) % 7
        return t < self.end_time and (not days or prev in days)

    def label(self) -> str:
        days = [int(d) for d in (self.weekdays or [])]
        window = f"{self.start_time:%H:%M}–{self.end_time:%H:%M}"
        if not days or len(days) == 7:
            return window
        names = [str(dict(WEEKDAYS)[d])[:3] for d in sorted(days)]
        return f"{window} ({', '.join(names)})"


class ComboComponent(TimeStampedModel):
    """A dish inside a combo / set menu; the warehouse consumes the components' recipes."""

    combo = models.ForeignKey("menu.MenuItem", on_delete=models.CASCADE, related_name="combo_components")
    item = models.ForeignKey("menu.MenuItem", on_delete=models.CASCADE, related_name="in_combos")
    quantity = models.PositiveSmallIntegerField(default=1)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "menu_combo_components"
        unique_together = [("combo", "item")]
        ordering = ["display_order"]
        verbose_name = _("Combo component")
        verbose_name_plural = _("Combo components")

    def __str__(self):
        return f"{self.quantity} × {self.item}"


class Promotion(TimeStampedModel):
    KIND_CHOICES = [("happy_hour", _("Happy hour (automatic)")), ("promo_code", _("Promo code"))]
    MODE_CHOICES = [("percent", _("Percent off")), ("fixed", _("Fixed amount off"))]
    APPLIES_CHOICES = [
        ("all", _("Whole order")),
        ("categories", _("Selected categories")),
        ("items", _("Selected dishes")),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="promotions")
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True, default="", help_text=_("Shown to guests on the menu."))
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="happy_hour")
    is_active = models.BooleanField(default=True)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default="percent")
    value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    applies_to = models.CharField(max_length=12, choices=APPLIES_CHOICES, default="all")
    categories = models.ManyToManyField("menu.MenuCategory", blank=True, related_name="promotions")
    items = models.ManyToManyField("menu.MenuItem", blank=True, related_name="promotions")
    schedule = models.ForeignKey(
        MenuSchedule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="promotions",
        help_text=_("Happy hour: only inside this window. Promo codes: optional restriction."),
    )
    code = models.CharField(max_length=30, blank=True, default="", help_text=_("Guests type this at checkout."))
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    min_order_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_uses = models.PositiveIntegerField(default=0, help_text=_("0 = unlimited."))
    max_uses_per_customer = models.PositiveIntegerField(default=0, help_text=_("0 = unlimited."))
    uses_count = models.PositiveIntegerField(default=0)
    channels = models.JSONField(default=list, blank=True, help_text=_("Empty = everywhere (web, QR, POS)."))
    stackable = models.BooleanField(default=False, help_text=_("May combine with other promotions on one order."))

    class Meta:
        db_table = "promotions"
        ordering = ["-is_active", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant", "code"], condition=~models.Q(code=""), name="promotion_code_unique_per_restaurant"
            )
        ]
        verbose_name = _("Promotion")
        verbose_name_plural = _("Promotions")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    def is_live(self, local_dt=None, *, channel: str = "") -> bool:
        local_dt = local_dt or timezone.localtime()
        if not self.is_active:
            return False
        today = local_dt.date()
        if self.starts_on and today < self.starts_on:
            return False
        if self.ends_on and today > self.ends_on:
            return False
        if self.max_uses and self.uses_count >= self.max_uses:
            return False
        if channel and self.channels and channel not in self.channels:
            return False
        if self.schedule_id and not self.schedule.matches(local_dt):
            return False
        return True

    def applies_to_item(self, item, *, category_ids=None, item_ids=None) -> bool:
        if self.applies_to == "all":
            return True
        if self.applies_to == "categories":
            ids = category_ids if category_ids is not None else set(self.categories.values_list("pk", flat=True))
            return item.category_id in ids
        ids = item_ids if item_ids is not None else set(self.items.values_list("pk", flat=True))
        return item.pk in ids

    def discount_for(self, amount: Decimal) -> Decimal:
        amount = Decimal(amount or 0)
        if amount <= 0:
            return Decimal("0")
        if self.mode == "percent":
            off = amount * min(self.value, Decimal("100")) / Decimal("100")
        else:
            off = min(self.value, amount)
        return off.quantize(Decimal("0.01"))


class PromotionUse(TimeStampedModel):
    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name="uses")
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="promotion_uses")
    customer = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    phone = models.CharField(max_length=20, blank=True, default="")
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        db_table = "promotion_uses"
        unique_together = [("promotion", "order")]
        verbose_name = _("Promotion use")
        verbose_name_plural = _("Promotion uses")
