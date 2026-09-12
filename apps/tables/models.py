"""
Table models for restaurant table management and QR code ordering.
"""

import hashlib
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

from .qr_links import DESTINATION_AUTO, DESTINATION_CHOICES, DESTINATION_CUSTOM, short_link, validate_custom_url


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

    # Set when this section mirrors a shared venue section (see apps.venues).
    venue_section = models.ForeignKey(
        "venues.VenueSection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mirrors",
    )

    @property
    def venue_managed(self):
        return self.venue_section_id is not None

    class Meta:
        db_table = "table_sections"
        ordering = ["display_order", "name"]
        unique_together = ["restaurant", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant", "venue_section"],
                condition=Q(venue_section__isnull=False),
                name="uniq_section_mirror_per_member",
            )
        ]
        verbose_name = _("Table Section")
        verbose_name_plural = _("Table Sections")

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

    # Set when this table mirrors a shared venue table (see apps.venues). The
    # venue registry owns number/name/capacity/shape; status and position stay
    # this restaurant's own.
    venue_table = models.ForeignKey(
        "venues.VenueTable",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mirrors",
    )

    @property
    def venue_managed(self):
        return self.venue_table_id is not None

    class Meta:
        db_table = "tables"
        ordering = ["section__display_order", "number"]
        unique_together = ["restaurant", "number"]
        constraints = [
            models.UniqueConstraint(
                fields=["restaurant", "venue_table"],
                condition=Q(venue_table__isnull=False),
                name="uniq_mirror_per_member",
            )
        ]
        verbose_name = _("Table")
        verbose_name_plural = _("Tables")
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
    qr_image = models.ImageField(
        upload_to="qr_codes/",
        blank=True,
        null=True,
        help_text="Generated QR code image",
    )
    is_active = models.BooleanField(default=True)
    scans_count = models.PositiveIntegerField(default=0)
    last_scanned_at = models.DateTimeField(null=True, blank=True)

    # Dynamic destination (see apps.tables.qr_links). The printed code encodes
    # a short link; where it goes is decided at scan time from these fields
    # and the table's current state.
    destination = models.CharField(max_length=16, choices=DESTINATION_CHOICES, default=DESTINATION_AUTO)
    custom_url = models.URLField(
        max_length=2000,
        blank=True,
        validators=[URLValidator(schemes=["http", "https"])],
        help_text="Only used when destination is 'Custom URL'.",
    )
    # What the stored PNG encodes. Images printed before short links exist
    # encode the direct URL; they keep working but cannot be re-pointed.
    qr_image_url = models.CharField(max_length=2000, blank=True, default="")
    # Short-link hits; scans_count stays the validate-endpoint count.
    resolves_count = models.PositiveIntegerField(default=0)
    last_resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "table_qr_codes"
        verbose_name = _("Table QR Code")
        verbose_name_plural = _("Table QR Codes")

    def __str__(self):
        return f"QR for {self.table} ({self.code[:8]}...)"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)
        # Generate QR code image after save if not exists
        if not self.qr_image:
            self.generate_qr_image()

    def clean(self):
        super().clean()
        if self.destination == DESTINATION_CUSTOM:
            try:
                self.custom_url = validate_custom_url(self.custom_url)
            except ValidationError as exc:
                raise ValidationError({"custom_url": exc.messages})
        else:
            self.custom_url = ""

    def get_qr_url(self):
        """What new QR images encode: the short link {FRONTEND_BASE_URL}/q/{code}."""
        return short_link(self.code)

    def direct_url(self):
        """The legacy direct URL ({base}/restaurant/{slug}?table={code}); still valid, not re-pointable."""
        base = settings.FRONTEND_BASE_URL.rstrip("/")
        return f"{base}/restaurant/{self.table.restaurant.slug}?table={self.code}"

    @property
    def image_is_current(self):
        return bool(self.qr_image) and self.qr_image_url == self.get_qr_url()

    def generate_qr_image(self):
        """Render and store the QR image for the current short link."""
        from django.core.files.base import ContentFile

        from .qr import render_qr_png

        url = self.get_qr_url()
        # A new file name per encoded URL: object stores and browsers cache the
        # old bytes under the old name.
        digest = hashlib.sha1(url.encode()).hexdigest()[:6]
        filename = f"qr_{self.table.restaurant.slug}_{self.table.number}_{self.code[:8]}_{digest}.png"
        self.qr_image_url = url
        self.qr_image.save(filename, ContentFile(render_qr_png(url)), save=False)
        self.save(update_fields=["qr_image", "qr_image_url", "updated_at"])

    def regenerate_qr_image(self):
        """Replace the stored image (e.g. a legacy direct-URL image) with a short-link one."""
        if self.qr_image:
            self.qr_image.delete(save=False)
        self.generate_qr_image()

    def record_resolve(self):
        """Count a short-link hit."""
        from django.utils import timezone

        self.resolves_count += 1
        self.last_resolved_at = timezone.now()
        self.save(update_fields=["resolves_count", "last_resolved_at"])

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
    PAYMENT_MODE_CHOICES = [
        ("split", "Everyone pays their own"),
        ("host_covers", "Host covers the whole table"),
    ]
    payment_mode = models.CharField(
        max_length=16,
        choices=PAYMENT_MODE_CHOICES,
        default="split",
        help_text=(
            "split = each guest pays for their own orders. "
            "host_covers = the host settles the entire table at the end; "
            "guest orders are created without an individual BOG charge."
        ),
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

    @property
    def restaurant(self):
        """The restaurant this session belongs to (through its table)."""
        return self.table.restaurant

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
    guest_contact = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Phone or email for anonymous guests so the host can reach them.",
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
