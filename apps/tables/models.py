"""
Table models for restaurant table management and QR code ordering.
"""

import secrets

from django.db import models

from apps.core.models import TimeStampedModel


class TableSection(TimeStampedModel):
    """
    Section/area within a restaurant (e.g., Main Hall, Terrace, VIP Room).
    Helps organize tables by location.
    """

    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="table_sections",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "table_sections"
        ordering = ["display_order", "name"]
        unique_together = ["restaurant", "name"]

    def __str__(self):
        return f"{self.name} @ {self.restaurant.name}"


class Table(TimeStampedModel):
    """
    Restaurant table with capacity, status, and QR code for ordering.
    """

    STATUS_CHOICES = [
        ("available", "Available"),
        ("occupied", "Occupied"),
        ("reserved", "Reserved"),
        ("unavailable", "Unavailable"),
    ]

    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="tables",
    )
    section = models.ForeignKey(
        TableSection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tables",
    )
    number = models.CharField(
        max_length=20,
        help_text="Table number or identifier (e.g., '1', 'A1', 'VIP-1')",
    )
    name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Optional friendly name (e.g., 'Window Table', 'Corner Booth')",
    )
    capacity = models.PositiveSmallIntegerField(
        default=4,
        help_text="Maximum number of guests",
    )
    min_capacity = models.PositiveSmallIntegerField(
        default=1,
        help_text="Minimum number of guests for reservation",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="available",
    )
    is_active = models.BooleanField(default=True)

    # Position for floor plan display (optional)
    position_x = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="X coordinate on floor plan",
    )
    position_y = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Y coordinate on floor plan",
    )

    # Table shape for floor plan
    shape = models.CharField(
        max_length=20,
        choices=[
            ("square", "Square"),
            ("round", "Round"),
            ("rectangle", "Rectangle"),
        ],
        default="square",
    )

    class Meta:
        db_table = "tables"
        ordering = ["section__display_order", "number"]
        unique_together = ["restaurant", "number"]
        indexes = [
            models.Index(fields=["restaurant", "status"]),
        ]

    def __str__(self):
        if self.name:
            return f"Table {self.number} ({self.name})"
        return f"Table {self.number}"

    @property
    def display_name(self) -> str:
        """Get display name for the table."""
        if self.name:
            return f"{self.number} - {self.name}"
        return f"Table {self.number}"

    def set_occupied(self):
        """Mark table as occupied."""
        self.status = "occupied"
        self.save(update_fields=["status", "updated_at"])

    def set_available(self):
        """Mark table as available."""
        self.status = "available"
        self.save(update_fields=["status", "updated_at"])


class TableQRCode(TimeStampedModel):
    """
    QR code for table ordering.
    Each table can have multiple QR codes for different purposes.
    """

    table = models.ForeignKey(
        Table,
        on_delete=models.CASCADE,
        related_name="qr_codes",
    )
    code = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
    )
    name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Optional name (e.g., 'Main QR', 'Tent Card')",
    )
    is_active = models.BooleanField(default=True)
    scans_count = models.PositiveIntegerField(default=0)
    last_scanned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "table_qr_codes"
        verbose_name = "Table QR Code"
        verbose_name_plural = "Table QR Codes"

    def __str__(self):
        return f"QR for {self.table} ({self.code[:8]}...)"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def record_scan(self):
        """Record a QR code scan."""
        from django.utils import timezone

        self.scans_count += 1
        self.last_scanned_at = timezone.now()
        self.save(update_fields=["scans_count", "last_scanned_at"])

    @classmethod
    def get_table_by_code(cls, code: str):
        """Get table by QR code, if valid."""
        try:
            qr = cls.objects.select_related("table", "table__restaurant").get(
                code=code,
                is_active=True,
                table__is_active=True,
            )
            return qr.table
        except cls.DoesNotExist:
            return None


class TableSession(TimeStampedModel):
    """
    Active session for a table, tracking guests and their orders.
    Created when guests sit down, closed when they leave.
    """

    STATUS_CHOICES = [
        ("active", "Active"),
        ("payment_pending", "Payment Pending"),
        ("closed", "Closed"),
    ]

    table = models.ForeignKey(
        Table,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    qr_code = models.ForeignKey(
        TableQRCode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
        help_text="QR code used to start this session",
    )
    host = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hosted_sessions",
        help_text="User who started this session",
    )
    invite_code = models.CharField(
        max_length=8,
        unique=True,
        blank=True,
        null=True,
        help_text="Shareable code to invite others to this session",
    )
    guest_count = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="active",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "table_sessions"
        ordering = ["-started_at"]

    def __str__(self):
        return f"Session at {self.table} ({self.started_at.strftime('%Y-%m-%d %H:%M')})"

    def close(self):
        """Close this session."""
        from django.utils import timezone

        self.status = "closed"
        self.closed_at = timezone.now()
        self.save(update_fields=["status", "closed_at", "updated_at"])
        self.table.set_available()

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def duration(self):
        """Get session duration as timedelta."""
        from django.utils import timezone

        end_time = self.closed_at or timezone.now()
        return end_time - self.started_at

    @property
    def duration_minutes(self) -> int:
        """Get session duration in minutes."""
        return int(self.duration.total_seconds() / 60)

    def save(self, *args, **kwargs):
        if not self.invite_code:
            self.invite_code = self._generate_invite_code()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_invite_code():
        """Generate 8-character alphanumeric invite code."""
        import string

        chars = string.ascii_uppercase + string.digits
        while True:
            code = "".join(secrets.choice(chars) for _ in range(8))
            if not TableSession.objects.filter(invite_code=code).exists():
                return code

    def get_or_create_guest(self, user=None, guest_name=""):
        """Get or create a guest record for this session."""
        if user and user.is_authenticated:
            guest, created = self.guests.get_or_create(
                user=user,
                defaults={"guest_name": guest_name or "", "is_host": self.host == user},
            )
        else:
            # For anonymous guests, always create new
            guest = self.guests.create(guest_name=guest_name or "Guest", is_host=False)
            created = True
        return guest, created


class TableSessionGuest(TimeStampedModel):
    """
    Individual guest at a table session.
    Tracks who joined and their orders.
    """

    STATUS_CHOICES = [
        ("active", "Active"),
        ("left", "Left"),
    ]

    session = models.ForeignKey(
        TableSession,
        on_delete=models.CASCADE,
        related_name="guests",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="table_session_guests",
    )
    guest_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="Name for anonymous guests",
    )
    is_host = models.BooleanField(
        default=False,
        help_text="Whether this guest is the session host",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="active",
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "table_session_guests"
        ordering = ["-is_host", "joined_at"]
        unique_together = [["session", "user"]]

    def __str__(self):
        name = self.user.email if self.user else self.guest_name
        return f"{name} at {self.session}"

    @property
    def display_name(self) -> str:
        """Get display name for the guest."""
        if self.user:
            return self.user.email
        return self.guest_name or "Guest"

    def leave(self):
        """Mark guest as left."""
        from django.utils import timezone

        self.status = "left"
        self.left_at = timezone.now()
        self.save(update_fields=["status", "left_at", "updated_at"])
