"""
Per-restaurant customer records built from orders, reservations and reviews;
segments, campaigns and automations that message them (through the
notifications module) with explicit consent.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

CHANNELS = [("sms", "SMS"), ("email", _("Email"))]


class Customer(TimeStampedModel):
    """One guest of one restaurant, keyed by phone (guests) or user (accounts)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="customers")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="restaurant_customers"
    )
    phone = models.CharField(max_length=20, blank=True, default="", db_index=True)
    email = models.EmailField(blank=True, default="")
    name = models.CharField(max_length=200, blank=True, default="")
    birthday = models.DateField(null=True, blank=True)
    language = models.CharField(max_length=2, blank=True, default="")
    tags = models.JSONField(default=list, blank=True, help_text=_("Free tags, e.g. vip, corporate, vegan."))
    notes = models.TextField(blank=True, default="")
    marketing_opt_in = models.BooleanField(default=False)
    opt_in_at = models.DateTimeField(null=True, blank=True)
    opt_in_source = models.CharField(max_length=30, blank=True, default="")
    opt_out_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(
        max_length=20, blank=True, default="", help_text=_("First contact: qr, web, pos, reservation…")
    )
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_visit_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_order_at = models.DateTimeField(null=True, blank=True)
    visits = models.PositiveIntegerField(default=0)
    orders_count = models.PositiveIntegerField(default=0)
    reservations_count = models.PositiveIntegerField(default=0)
    reviews_count = models.PositiveIntegerField(default=0)
    total_spend = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    avg_ticket = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    last_rating = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "crm_customers"
        ordering = ["-last_visit_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant", "phone"], condition=~models.Q(phone=""), name="crm_customer_phone_unique"
            ),
            models.UniqueConstraint(
                fields=["restaurant", "user"], condition=models.Q(user__isnull=False), name="crm_customer_user_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["restaurant", "marketing_opt_in"]),
            models.Index(fields=["restaurant", "birthday"]),
        ]
        verbose_name = _("Customer")
        verbose_name_plural = _("Customers")

    def __str__(self):
        return self.name or self.phone or self.email or str(self.pk)[:8]

    @property
    def can_sms(self) -> bool:
        return self.marketing_opt_in and bool(self.phone)

    @property
    def can_email(self) -> bool:
        return self.marketing_opt_in and bool(self.email)

    @property
    def days_since_visit(self) -> int | None:
        if not self.last_visit_at:
            return None
        return (timezone.now() - self.last_visit_at).days


class Segment(TimeStampedModel):
    """A saved filter over customers; ``rules`` are evaluated by services.segment_queryset."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="segments")
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, blank=True, default="")
    rules = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "Keys: min_visits, max_visits, min_spend, last_visit_days_max (visited within N days), "
            "last_visit_days_min (no visit for N days), tags_any (list), birthday_month (1-12), has_email, has_phone, opt_in (default true)."
        ),
    )
    is_builtin = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "crm_segments"
        ordering = ["name"]
        unique_together = [("restaurant", "name")]
        verbose_name = _("Segment")
        verbose_name_plural = _("Segments")

    def __str__(self):
        return self.name


class Campaign(TimeStampedModel):
    STATUS_CHOICES = [
        ("draft", _("Draft")),
        ("scheduled", _("Scheduled")),
        ("sending", _("Sending")),
        ("sent", _("Sent")),
        ("cancelled", _("Cancelled")),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="campaigns")
    name = models.CharField(max_length=120)
    channel = models.CharField(max_length=10, choices=CHANNELS, default="sms")
    segment = models.ForeignKey(Segment, on_delete=models.PROTECT, related_name="campaigns")
    subject = models.CharField(max_length=150, blank=True, default="", help_text=_("Email only."))
    body = models.TextField(help_text=_("Placeholders: {name} {restaurant} {code} {link} {unsubscribe}"))
    promotion = models.ForeignKey(
        "promotions.Promotion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
        help_text=_("A promo code to include as {code}."),
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="draft", db_index=True)
    audience_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "crm_campaigns"
        ordering = ["-created_at"]
        verbose_name = _("Campaign")
        verbose_name_plural = _("Campaigns")

    def __str__(self):
        return self.name


class CampaignDelivery(TimeStampedModel):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="deliveries")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="campaign_deliveries")
    message = models.ForeignKey(
        "notifications.OutboundMessage", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    status = models.CharField(max_length=10, default="queued")  # queued | sent | failed | skipped

    class Meta:
        db_table = "crm_campaign_deliveries"
        unique_together = [("campaign", "customer")]


class Automation(TimeStampedModel):
    """Always-on messages: birthday greeting, review prompt after a visit, win-back for lapsed guests."""

    KIND_CHOICES = [
        ("birthday", _("Birthday greeting")),
        ("review_prompt", _("Review prompt after a visit")),
        ("winback", _("Win-back for lapsed guests")),
    ]
    DEFAULT_BODY = {
        "birthday": "{restaurant}: happy birthday, {name}! Come celebrate with us — show this message for a treat. {code}",
        "review_prompt": "{restaurant}: thank you for visiting! Tell us how it was: {link}",
        "winback": "{restaurant}: we miss you, {name}! Here is something for your next visit: {code}",
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="automations")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    enabled = models.BooleanField(default=False)
    channel = models.CharField(max_length=10, choices=CHANNELS, default="sms")
    body = models.TextField(
        blank=True, default="", help_text=_("Placeholders: {name} {restaurant} {code} {link} {unsubscribe}")
    )
    promotion = models.ForeignKey(
        "promotions.Promotion", on_delete=models.SET_NULL, null=True, blank=True, related_name="automations"
    )
    delay_hours = models.PositiveSmallIntegerField(default=3, help_text=_("Review prompt: hours after the visit."))
    lapsed_days = models.PositiveSmallIntegerField(default=45, help_text=_("Win-back: days without a visit."))
    sent_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "crm_automations"
        unique_together = [("restaurant", "kind")]
        verbose_name = _("Automation")
        verbose_name_plural = _("Automations")

    def __str__(self):
        return f"{self.get_kind_display()} ({'on' if self.enabled else 'off'})"

    def template(self) -> str:
        return self.body or self.DEFAULT_BODY.get(self.kind, "")


class AutomationSend(TimeStampedModel):
    """One automated message to one customer (idempotency: kind + customer + key)."""

    automation = models.ForeignKey(Automation, on_delete=models.CASCADE, related_name="sends")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="automation_sends")
    key = models.CharField(
        max_length=64, help_text=_("Order id for review prompts, year for birthdays, month for win-backs.")
    )
    message = models.ForeignKey(
        "notifications.OutboundMessage", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "crm_automation_sends"
        unique_together = [("automation", "customer", "key")]


ZERO = Decimal("0")
