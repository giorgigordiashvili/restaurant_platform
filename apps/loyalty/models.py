"""
Loyalty / punch-card models.

Restaurants define LoyaltyProgram ("buy N of X, get M of Y free"). When an
order is completed, the signal in signals.py increments a LoyaltyCounter
for the customer (resolved by user FK or by phone-number match). Once the
counter hits `threshold`, the customer can generate a LoyaltyRedemption
(short-lived code) that the restaurant's staff validates + confirms on the
POS. Confirming decrements the counter by `threshold` so the surplus rolls
over into the next card.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class LoyaltyProgram(TimeStampedModel):
    """One restaurant's punch-card promotion."""

    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="loyalty_programs",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)

    trigger_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.PROTECT,
        related_name="loyalty_trigger_programs",
        help_text="Buying this item earns punches.",
    )
    threshold = models.PositiveIntegerField(default=5, help_text="Punches required to unlock the reward.")
    reward_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.PROTECT,
        related_name="loyalty_reward_programs",
        help_text="Item the customer gets free when the threshold is hit.",
    )
    reward_quantity = models.PositiveIntegerField(default=1)

    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    code_ttl_seconds = models.PositiveIntegerField(
        default=86_400,
        help_text="How long a generated redemption code is valid.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} @ {self.restaurant_id}"

    def is_live(self, now=None) -> bool:
        now = now or timezone.now()
        if not self.is_active:
            return False
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True


class LoyaltyCounter(TimeStampedModel):
    """
    Per-customer punch balance for one program.

    `user` OR `phone_number` is set (exclusive). When a completed order's
    customer is authenticated we link by `user`; for phone-only customers
    we store their phone and later merge if they sign up.
    """

    program = models.ForeignKey(
        LoyaltyProgram,
        on_delete=models.CASCADE,
        related_name="counters",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="loyalty_counters",
    )
    phone_number = models.CharField(max_length=32, blank=True, default="", db_index=True)
    punches = models.PositiveIntegerField(default=0)
    last_earned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["program", "user"],
                condition=models.Q(user__isnull=False),
                name="loyalty_counter_unique_program_user",
            ),
            models.UniqueConstraint(
                fields=["program", "phone_number"],
                condition=models.Q(user__isnull=True) & ~models.Q(phone_number=""),
                name="loyalty_counter_unique_program_phone",
            ),
        ]

    def __str__(self) -> str:
        target = self.user_id or self.phone_number or "anon"
        return f"{self.program_id} · {target} · {self.punches}"

    @property
    def can_redeem(self) -> bool:
        return self.punches >= self.program.threshold


class LoyaltyRedemption(TimeStampedModel):
    """Audit row + unique code for one redemption attempt."""

    STATUS_PENDING = "pending"
    STATUS_REDEEMED = "redeemed"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_REDEEMED, "Redeemed"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    program = models.ForeignKey(
        LoyaltyProgram,
        on_delete=models.CASCADE,
        related_name="redemptions",
    )
    counter = models.ForeignKey(
        LoyaltyCounter,
        on_delete=models.CASCADE,
        related_name="redemptions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_redemptions",
    )
    phone_number = models.CharField(max_length=32, blank=True, default="")

    code = models.CharField(max_length=64, unique=True, db_index=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    issued_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    redeemed_at = models.DateTimeField(null=True, blank=True)
    redeemed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_confirmations",
    )
    redeemed_order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_redemptions",
    )

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self) -> str:
        return f"{self.code} · {self.status}"

    def save(self, *args, **kwargs):
        if not self.code:
            # 32 URL-safe bytes -> ~43 chars. Collision chance is negligible but
            # still loop for safety.
            for _ in range(5):
                candidate = secrets.token_urlsafe(24)
                if not LoyaltyRedemption.objects.filter(code=candidate).exists():
                    self.code = candidate
                    break
            else:
                raise RuntimeError("Could not generate a unique loyalty code")
        if not self.expires_at:
            ttl = self.program.code_ttl_seconds if self.program_id else 86_400
            self.expires_at = self.issued_at + timezone.timedelta(seconds=ttl)
        super().save(*args, **kwargs)

    @property
    def is_usable(self) -> bool:
        if self.status != self.STATUS_PENDING:
            return False
        return timezone.now() <= self.expires_at
