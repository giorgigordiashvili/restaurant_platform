"""
Staff notifications (in-app + Expo push + optional email) and guest
messages (SMS / email through a pluggable provider). Nothing here talks to
the network; see services / providers / tasks.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

DEFAULT_TEMPLATES = {
    "reservation_confirmation_ka": "{restaurant}: თქვენი მაგიდა დაჯავშნილია {date} {time}-ზე, {guests} სტუმარი. კოდი {code}.",
    "reservation_confirmation_en": "{restaurant}: your table is booked for {date} at {time}, {guests} guests. Code {code}.",
    "reservation_reminder_ka": "{restaurant}: შეგახსენებთ, თქვენი ჯავშანი {date} {time}-ზეა. გელოდებით!",
    "reservation_reminder_en": "{restaurant}: reminder, your reservation is on {date} at {time}. See you soon!",
    "review_prompt_ka": "{restaurant}: გმადლობთ სტუმრობისთვის! გაგვიზიარეთ შთაბეჭდილება: {link}",
    "review_prompt_en": "{restaurant}: thank you for visiting! Tell us how it was: {link}",
    "order_accepted_ka": "{restaurant}: თქვენი შეკვეთა {order} მიღებულია, მზად იქნება დაახლოებით {time}-ზე. {link}",
    "order_accepted_en": "{restaurant}: your order {order} is accepted and will be ready around {time}. {link}",
    "order_ready_ka": "{restaurant}: თქვენი შეკვეთა {order} მზადაა, გელოდებით!",
    "order_ready_en": "{restaurant}: your order {order} is ready for pickup!",
    "order_on_the_way_ka": "{restaurant}: შეკვეთა {order} გზაშია. თვალი ადევნეთ: {tracking_url}",
    "order_on_the_way_en": "{restaurant}: order {order} is on its way. Track it: {tracking_url}",
}


class Device(TimeStampedModel):
    """A staff phone / tablet that can receive push notifications."""

    KIND_CHOICES = [("expo", "Expo push"), ("web", "Web push")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_devices")
    restaurant = models.ForeignKey(
        "tenants.Restaurant", on_delete=models.SET_NULL, null=True, blank=True, related_name="push_devices"
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="expo")
    token = models.CharField(max_length=500, unique=True)
    platform = models.CharField(max_length=20, blank=True, default="")
    app_version = models.CharField(max_length=40, blank=True, default="")
    last_seen_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    last_error = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "notification_devices"
        verbose_name = _("Push device")
        verbose_name_plural = _("Push devices")

    def __str__(self):
        return f"{self.kind} {self.platform} {self.token[:12]}…"


class Notification(TimeStampedModel):
    """One in-app notification for one staff user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="notifications")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    event = models.CharField(max_length=40, db_index=True)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    data = models.JSONField(default=dict, blank=True, help_text=_("Deep-link payload for the POS (kind, id)."))
    url = models.CharField(max_length=300, blank=True, default="", help_text=_("Admin page to open."))
    dedupe_key = models.CharField(max_length=150, blank=True, default="", db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    pushed_at = models.DateTimeField(null=True, blank=True)
    push_error = models.CharField(max_length=200, blank=True, default="")
    emailed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "read_at", "-created_at"]),
            models.Index(fields=["restaurant", "-created_at"]),
        ]
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")

    def __str__(self):
        return f"{self.event}: {self.title}"


class StaffNotificationPrefs(TimeStampedModel):
    """What one staff member wants to hear about at one restaurant."""

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="notification_prefs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_prefs")
    muted_events = models.JSONField(default=list, blank=True)
    push = models.BooleanField(default=True)
    email = models.BooleanField(default=False)
    quiet_from = models.TimeField(null=True, blank=True, help_text=_("No push between these hours (in-app still)."))
    quiet_to = models.TimeField(null=True, blank=True)

    class Meta:
        db_table = "notification_staff_prefs"
        unique_together = [("restaurant", "user")]
        verbose_name = _("Staff notification preferences")
        verbose_name_plural = _("Staff notification preferences")

    def __str__(self):
        return f"{self.user} @ {self.restaurant}"


class RestaurantNotificationSettings(TimeStampedModel):
    """Guest messaging switches and message templates, one row per restaurant."""

    restaurant = models.OneToOneField(
        "tenants.Restaurant", on_delete=models.CASCADE, related_name="notification_settings"
    )
    guest_sms = models.BooleanField(default=False, help_text=_("Send SMS to guests (needs an SMS provider)."))
    guest_email = models.BooleanField(default=True, help_text=_("Send email to guests when they gave an address."))
    sender_name = models.CharField(max_length=60, blank=True, default="", help_text=_("Shown as the restaurant name."))
    reservation_confirmation_ka = models.TextField(default=DEFAULT_TEMPLATES["reservation_confirmation_ka"])
    reservation_confirmation_en = models.TextField(default=DEFAULT_TEMPLATES["reservation_confirmation_en"])
    reservation_reminder_ka = models.TextField(default=DEFAULT_TEMPLATES["reservation_reminder_ka"])
    reservation_reminder_en = models.TextField(default=DEFAULT_TEMPLATES["reservation_reminder_en"])
    review_prompt_ka = models.TextField(default=DEFAULT_TEMPLATES["review_prompt_ka"])
    review_prompt_en = models.TextField(default=DEFAULT_TEMPLATES["review_prompt_en"])
    order_accepted_ka = models.TextField(default=DEFAULT_TEMPLATES["order_accepted_ka"])
    order_accepted_en = models.TextField(default=DEFAULT_TEMPLATES["order_accepted_en"])
    order_ready_ka = models.TextField(default=DEFAULT_TEMPLATES["order_ready_ka"])
    order_ready_en = models.TextField(default=DEFAULT_TEMPLATES["order_ready_en"])
    order_on_the_way_ka = models.TextField(default=DEFAULT_TEMPLATES["order_on_the_way_ka"])
    order_on_the_way_en = models.TextField(default=DEFAULT_TEMPLATES["order_on_the_way_en"])

    class Meta:
        db_table = "notification_settings"
        verbose_name = _("Notification settings")
        verbose_name_plural = _("Notification settings")

    def __str__(self):
        return f"Notification settings for {self.restaurant}"

    def template(self, kind: str, language: str) -> str:
        lang = language if language in ("ka", "en") else "ka"
        return getattr(self, f"{kind}_{lang}", "") or DEFAULT_TEMPLATES.get(f"{kind}_{lang}", "")


class OutboundMessage(TimeStampedModel):
    """One SMS / email to a guest (or a supplier), with the provider's verdict."""

    CHANNEL_CHOICES = [("sms", "SMS"), ("email", "Email")]
    STATUS_CHOICES = [
        ("queued", _("Queued")),
        ("sent", _("Sent")),
        ("failed", _("Failed")),
        ("skipped", _("Skipped")),
    ]
    KIND_CHOICES = [
        ("reservation_confirmation", _("Reservation confirmation")),
        ("reservation_reminder", _("Reservation reminder")),
        ("review_prompt", _("Review prompt")),
        ("campaign", _("Marketing campaign")),
        ("automation", _("Marketing automation")),
        ("purchase_order", _("Purchase order")),
        ("order_accepted", _("Order accepted")),
        ("order_ready", _("Order ready")),
        ("order_on_the_way", _("Order on its way")),
        ("test", _("Test message")),
        ("other", _("Other")),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="outbound_messages")
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    to = models.CharField(max_length=254)
    subject = models.CharField(max_length=200, blank=True, default="")
    body = models.TextField()
    kind = models.CharField(max_length=30, choices=KIND_CHOICES, default="other")
    ref_model = models.CharField(max_length=60, blank=True, default="")
    ref_id = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="queued", db_index=True)
    provider = models.CharField(max_length=30, blank=True, default="")
    provider_id = models.CharField(max_length=120, blank=True, default="")
    error = models.CharField(max_length=300, blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "notification_outbound_messages"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["restaurant", "-created_at"]),
            models.Index(fields=["ref_model", "ref_id"]),
        ]
        verbose_name = _("Outbound message")
        verbose_name_plural = _("Outbound messages")

    def __str__(self):
        return f"{self.channel} to {self.to} ({self.status})"


class NotificationSettingsPage(RestaurantNotificationSettings):
    """Proxy: the 'Notifications' settings page in the tenant admin."""

    class Meta:
        proxy = True
        verbose_name = _("Notification settings")
        verbose_name_plural = _("Notification settings")
