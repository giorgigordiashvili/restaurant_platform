"""
Waitlist & walk-ins: today's queue at the door. Guests are added by staff
(or join from a QR on their phone), get a quoted wait, an SMS when the table
is ready, and are seated straight into a table session.
"""

from __future__ import annotations

import secrets

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


def new_token() -> str:
    return secrets.token_urlsafe(16)


class WaitlistSettings(TimeStampedModel):
    restaurant = models.OneToOneField("tenants.Restaurant", on_delete=models.CASCADE, related_name="waitlist_settings")
    default_wait_minutes = models.PositiveSmallIntegerField(
        default=15, help_text=_("Quoted wait when there is no history to estimate from.")
    )
    notify_expire_minutes = models.PositiveSmallIntegerField(
        default=10, help_text=_("A notified guest who does not show up within this time is marked no-show.")
    )
    allow_self_join = models.BooleanField(default=True, help_text=_("Guests can join from the QR at the door."))
    max_party_size = models.PositiveSmallIntegerField(default=12)
    sms_on_join = models.BooleanField(default=True, help_text=_("Text the guest their place in line when added."))
    sms_on_ready = models.BooleanField(default=True, help_text=_("Text the guest when the table is ready."))
    join_token = models.CharField(max_length=40, default=new_token, editable=False)

    class Meta:
        db_table = "waitlist_settings"
        verbose_name = _("Waitlist settings")
        verbose_name_plural = _("Waitlist settings")

    def __str__(self):
        return f"Waitlist: {self.restaurant}"

    def rotate_token(self):
        self.join_token = new_token()
        self.save(update_fields=["join_token", "updated_at"])


class WaitlistEntry(TimeStampedModel):
    STATUS_CHOICES = [
        ("waiting", _("Waiting")),
        ("notified", _("Table ready · notified")),
        ("seated", _("Seated")),
        ("left", _("Left")),
        ("cancelled", _("Cancelled")),
        ("no_show", _("No-show")),
    ]
    SOURCE_CHOICES = [("pos", _("Staff")), ("self", _("Guest (QR)")), ("reservation", _("From a reservation"))]
    OPEN = ("waiting", "notified")

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="waitlist_entries")
    date = models.DateField(db_index=True, help_text=_("Local service day."))
    position = models.PositiveSmallIntegerField(default=0)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True, default="")
    party_size = models.PositiveSmallIntegerField(default=2)
    quoted_minutes = models.PositiveSmallIntegerField(default=15)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="waiting", db_index=True)
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES, default="pos")
    notes = models.CharField(max_length=200, blank=True, default="")
    added_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    notified_at = models.DateTimeField(null=True, blank=True)
    notify_count = models.PositiveSmallIntegerField(default=0)
    seated_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)
    table = models.ForeignKey(
        "tables.Table", on_delete=models.SET_NULL, null=True, blank=True, related_name="waitlist_entries"
    )
    session = models.ForeignKey(
        "tables.TableSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="waitlist_entries"
    )
    reservation = models.ForeignKey(
        "reservations.Reservation", on_delete=models.SET_NULL, null=True, blank=True, related_name="waitlist_entries"
    )
    token = models.CharField(max_length=40, default=new_token, unique=True, editable=False)
    estimated_ready_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "waitlist_entries"
        ordering = ["date", "position", "created_at"]
        indexes = [models.Index(fields=["restaurant", "date", "status", "position"])]
        verbose_name = _("Waitlist entry")
        verbose_name_plural = _("Waitlist")

    def __str__(self):
        return f"{self.name} ({self.party_size}) · {self.get_status_display()}"

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN

    @property
    def waited_minutes(self) -> int:
        from django.utils import timezone

        end = self.seated_at or self.left_at or timezone.now()
        return max(int((end - self.created_at).total_seconds() // 60), 0)


class WaitlistSettingsPage(WaitlistSettings):
    class Meta:
        proxy = True
        verbose_name = _("Waitlist settings")
        verbose_name_plural = _("Waitlist settings")
