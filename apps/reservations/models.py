"""
Reservation models for table bookings and scheduling.
"""

import secrets
import string
from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class ReservationSettings(TimeStampedModel):
    """
    Restaurant-specific reservation settings.
    """

    restaurant = models.OneToOneField(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="reservation_settings",
    )

    # Availability
    accepts_reservations = models.BooleanField(default=True)
    min_party_size = models.PositiveSmallIntegerField(default=1)
    max_party_size = models.PositiveSmallIntegerField(default=20)
    reservation_duration = models.DurationField(
        default=timedelta(hours=2),
        help_text="Default duration for a reservation",
    )

    # Booking window
    advance_booking_days = models.PositiveSmallIntegerField(
        default=30,
        help_text="How many days in advance can guests book",
    )
    min_advance_hours = models.PositiveSmallIntegerField(
        default=2,
        help_text="Minimum hours in advance required for booking",
    )
    buffer_minutes = models.PositiveSmallIntegerField(
        default=15,
        help_text="Buffer time between reservations on same table",
    )

    # Time slots
    slot_interval_minutes = models.PositiveSmallIntegerField(
        default=30,
        help_text="Time slot intervals (e.g., every 30 minutes)",
    )

    # Cancellation policy
    cancellation_deadline_hours = models.PositiveSmallIntegerField(
        default=24,
        help_text="Hours before reservation when cancellation is allowed",
    )

    # Confirmation
    require_confirmation = models.BooleanField(
        default=False,
        help_text="Require restaurant confirmation for reservations",
    )
    auto_confirm_threshold = models.PositiveSmallIntegerField(
        default=4,
        help_text="Auto-confirm reservations for parties up to this size",
    )

    # Notifications
    send_reminder = models.BooleanField(default=True)
    reminder_hours_before = models.PositiveSmallIntegerField(default=24)

    # Overbooking protection
    max_daily_reservations = models.PositiveSmallIntegerField(
        default=0,
        help_text="Maximum reservations per day (0 = unlimited)",
    )
    max_hourly_reservations = models.PositiveSmallIntegerField(
        default=0,
        help_text="Maximum reservations per hour (0 = unlimited)",
    )

    class Meta:
        db_table = "reservation_settings"
        verbose_name = "Reservation Settings"
        verbose_name_plural = "Reservation Settings"

    def __str__(self):
        return f"Reservation settings for {self.restaurant.name}"


class Reservation(TimeStampedModel):
    """
    Table reservation for a restaurant.
    """

    STATUS_CHOICES = [
        ("pending", "Pending Confirmation"),
        ("confirmed", "Confirmed"),
        ("waitlist", "Waitlisted"),
        ("seated", "Seated"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
        ("no_show", "No Show"),
    ]

    SOURCE_CHOICES = [
        ("website", "Website"),
        ("phone", "Phone"),
        ("walk_in", "Walk-in"),
        ("third_party", "Third-party Platform"),
        ("app", "Mobile App"),
    ]

    # Restaurant
    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="reservations",
    )

    # Customer
    customer = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reservations",
        help_text="Registered user making the reservation",
    )

    # Guest info (for non-registered users or phone bookings)
    guest_name = models.CharField(max_length=255)
    guest_email = models.EmailField(blank=True)
    guest_phone = models.CharField(max_length=20)

    # Reservation details
    reservation_date = models.DateField(db_index=True)
    reservation_time = models.TimeField(db_index=True)
    party_size = models.PositiveSmallIntegerField()
    duration = models.DurationField(
        default=timedelta(hours=2),
        help_text="Expected duration of the reservation",
    )

    # Table assignment
    table = models.ForeignKey(
        "tables.Table",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reservations",
    )

    # Status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default="website",
    )

    # Confirmation
    confirmation_code = models.CharField(
        max_length=12,
        unique=True,
        editable=False,
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_reservations",
    )

    # Notes
    special_requests = models.TextField(blank=True)
    internal_notes = models.TextField(
        blank=True,
        help_text="Private notes visible only to staff",
    )

    # Reminders
    reminder_sent = models.BooleanField(default=False)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)

    # Cancellation
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_reservations",
    )
    cancellation_reason = models.TextField(blank=True)

    # Arrival
    seated_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "reservations"
        verbose_name = "Reservation"
        verbose_name_plural = "Reservations"
        ordering = ["reservation_date", "reservation_time"]
        indexes = [
            models.Index(fields=["restaurant", "reservation_date"]),
            models.Index(fields=["restaurant", "status"]),
            models.Index(fields=["customer"]),
            models.Index(fields=["confirmation_code"]),
        ]

    def __str__(self):
        return f"Reservation {self.confirmation_code} - {self.guest_name} ({self.party_size} guests)"

    def save(self, *args, **kwargs):
        if not self.confirmation_code:
            self.confirmation_code = self._generate_confirmation_code()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_confirmation_code():
        """Generate a unique 8-character confirmation code."""
        chars = string.ascii_uppercase + string.digits
        while True:
            code = "".join(secrets.choice(chars) for _ in range(8))
            if not Reservation.objects.filter(confirmation_code=code).exists():
                return code

    @property
    def reservation_datetime(self):
        """Get combined datetime of reservation."""
        from datetime import datetime

        return timezone.make_aware(datetime.combine(self.reservation_date, self.reservation_time))

    @property
    def end_datetime(self):
        """Get expected end datetime of reservation."""
        return self.reservation_datetime + self.duration

    @property
    def is_upcoming(self):
        """Check if reservation is in the future."""
        return self.reservation_datetime > timezone.now()

    @property
    def is_past(self):
        """Check if reservation is in the past."""
        return self.reservation_datetime < timezone.now()

    @property
    def can_cancel(self):
        """Check if reservation can be cancelled by customer."""
        if self.status in ["cancelled", "completed", "no_show", "seated"]:
            return False
        if not self.is_upcoming:
            return False

        # Check cancellation deadline
        try:
            settings = self.restaurant.reservation_settings
            deadline = self.reservation_datetime - timedelta(hours=settings.cancellation_deadline_hours)
            return timezone.now() < deadline
        except ReservationSettings.DoesNotExist:
            return self.is_upcoming

    @property
    def can_modify(self):
        """Check if reservation can be modified by customer."""
        return self.status in ["pending", "confirmed", "waitlist"] and self.is_upcoming

    def confirm(self, confirmed_by=None):
        """Confirm the reservation."""
        self.status = "confirmed"
        self.confirmed_at = timezone.now()
        self.confirmed_by = confirmed_by
        self.save(update_fields=["status", "confirmed_at", "confirmed_by", "updated_at"])

    def cancel(self, cancelled_by=None, reason=""):
        """Cancel the reservation."""
        self.status = "cancelled"
        self.cancelled_at = timezone.now()
        self.cancelled_by = cancelled_by
        self.cancellation_reason = reason
        self.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancellation_reason",
                "updated_at",
            ]
        )

    def mark_seated(self):
        """Mark the customer as seated."""
        self.status = "seated"
        self.seated_at = timezone.now()
        self.save(update_fields=["status", "seated_at", "updated_at"])

    def mark_completed(self):
        """Mark the reservation as completed."""
        self.status = "completed"
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at", "updated_at"])

    def mark_no_show(self):
        """Mark the reservation as no-show."""
        self.status = "no_show"
        self.save(update_fields=["status", "updated_at"])


class ReservationBlockedTime(TimeStampedModel):
    """
    Blocked time slots when reservations are not accepted.
    Used for holidays, private events, maintenance, etc.
    """

    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="blocked_times",
    )

    # Time period
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()

    # Optional: specific tables only
    tables = models.ManyToManyField(
        "tables.Table",
        blank=True,
        related_name="blocked_times",
        help_text="Leave empty to block all tables",
    )

    # Reason
    REASON_CHOICES = [
        ("holiday", "Holiday"),
        ("private_event", "Private Event"),
        ("maintenance", "Maintenance"),
        ("staff_shortage", "Staff Shortage"),
        ("other", "Other"),
    ]
    reason = models.CharField(
        max_length=20,
        choices=REASON_CHOICES,
        default="other",
    )
    description = models.TextField(blank=True)

    # Created by
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_blocked_times",
    )

    # Recurring
    is_recurring = models.BooleanField(default=False)
    recurrence_pattern = models.JSONField(
        null=True,
        blank=True,
        help_text="Recurrence pattern (e.g., weekly, monthly)",
    )

    class Meta:
        db_table = "reservation_blocked_times"
        verbose_name = "Blocked Time"
        verbose_name_plural = "Blocked Times"
        ordering = ["start_datetime"]

    def __str__(self):
        return f"Blocked: {self.start_datetime} - {self.end_datetime} ({self.get_reason_display()})"

    @property
    def is_active(self):
        """Check if block is currently active."""
        now = timezone.now()
        return self.start_datetime <= now <= self.end_datetime

    @property
    def is_all_tables(self):
        """Check if all tables are blocked."""
        return not self.tables.exists()


class ReservationHistory(TimeStampedModel):
    """
    History of status changes for a reservation.
    """

    reservation = models.ForeignKey(
        "Reservation",
        on_delete=models.CASCADE,
        related_name="history",
    )
    previous_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "reservation_history"
        verbose_name = "Reservation History"
        verbose_name_plural = "Reservation Histories"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reservation.confirmation_code}: {self.previous_status} → {self.new_status}"
