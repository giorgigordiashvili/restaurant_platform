"""
Shared venues: several restaurants (and a bar) serving the same physical tables.

The venue owns the canonical table registry (VenueSection / VenueTable). Each
member restaurant keeps its own Table / TableSection rows, mirrored from the
registry and linked back through Table.venue_table / TableSection.venue_section,
so every existing per-restaurant scope (sessions, orders, payments, POS) is
untouched. Membership is a OneToOne on Restaurant: one venue per restaurant.
"""

import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

SHAPE_CHOICES = [
    ("square", _("Square")),
    ("round", _("Round")),
    ("rectangle", _("Rectangle")),
]


class Venue(TimeStampedModel):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=100, unique=True, db_index=True)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to="venues/logos/", blank=True, null=True)
    logo_blurhash = models.CharField(max_length=64, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "venues"
        ordering = ["name"]
        verbose_name = _("Venue")
        verbose_name_plural = _("Venues")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "venue"
            slug, n = base, 1
            while Venue.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                n += 1
                slug = f"{base}-{n}"
            self.slug = slug
        super().save(*args, **kwargs)

    def active_memberships(self):
        return self.memberships.filter(restaurant__is_active=True).select_related("restaurant")


class VenueMember(TimeStampedModel):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="memberships")
    # OneToOne: a restaurant can belong to at most one venue, enforced by the DB.
    restaurant = models.OneToOneField(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="venue_membership",
    )
    display_order = models.PositiveIntegerField(default=0)
    joined_at = models.DateTimeField(auto_now_add=True)
    is_layout_seed = models.BooleanField(
        default=False,
        help_text=_("This restaurant's tables seeded the venue layout (historical)."),
    )

    class Meta:
        db_table = "venue_members"
        ordering = ["display_order", "joined_at"]
        verbose_name = _("Venue member")
        verbose_name_plural = _("Venue members")

    def __str__(self):
        return f"{self.restaurant} @ {self.venue}"


class VenueSection(TimeStampedModel):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="sections")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "venue_sections"
        ordering = ["display_order", "name"]
        unique_together = ["venue", "name"]
        verbose_name = _("Venue section")
        verbose_name_plural = _("Venue sections")

    def __str__(self):
        return f"{self.venue}: {self.name}"


class VenueTable(TimeStampedModel):
    """A physical table shared by every restaurant at the venue."""

    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="tables")
    section = models.ForeignKey(
        VenueSection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tables",
    )
    number = models.CharField(max_length=20)
    name = models.CharField(max_length=100, blank=True)
    capacity = models.PositiveSmallIntegerField(default=4)
    min_capacity = models.PositiveSmallIntegerField(default=1)
    shape = models.CharField(max_length=10, choices=SHAPE_CHOICES, default="square")
    is_active = models.BooleanField(default=True)
    # The venue's own QR code: this is the one printed on the physical table.
    code = models.CharField(max_length=64, unique=True, db_index=True)
    qr_image = models.ImageField(upload_to="venue_qr_codes/", blank=True, null=True)
    scans_count = models.PositiveIntegerField(default=0)
    last_scanned_at = models.DateTimeField(null=True, blank=True)
    # What the stored PNG encodes (short link vs legacy direct URL) and
    # short-link hit counts; see apps.tables.qr_links.
    qr_image_url = models.CharField(max_length=2000, blank=True, default="")
    resolves_count = models.PositiveIntegerField(default=0)
    last_resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "venue_tables"
        ordering = ["section__display_order", "number"]
        unique_together = ["venue", "number"]
        verbose_name = _("Venue table")
        verbose_name_plural = _("Venue tables")

    def __str__(self):
        return f"{self.venue}: table {self.number}"

    @staticmethod
    def generate_code():
        # Distinct namespace from TableQRCode.code, and checked against it too:
        # both kinds of code are pasted into scan endpoints interchangeably.
        from apps.tables.models import TableQRCode

        while True:
            code = "v_" + secrets.token_urlsafe(24)
            if not VenueTable.objects.filter(code=code).exists() and not TableQRCode.objects.filter(code=code).exists():
                return code

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.generate_code()
        super().save(*args, **kwargs)
        if not self.qr_image:
            self.generate_qr_image()

    def get_qr_url(self):
        """What new QR images encode: the short link {FRONTEND_BASE_URL}/q/{code}."""
        from apps.tables.qr_links import short_link

        return short_link(self.code)

    def direct_url(self):
        """The legacy direct URL ({base}/venue/{slug}?table={code})."""
        base = settings.FRONTEND_BASE_URL.rstrip("/")
        return f"{base}/venue/{self.venue.slug}?table={self.code}"

    @property
    def image_is_current(self):
        return bool(self.qr_image) and self.qr_image_url == self.get_qr_url()

    def generate_qr_image(self):
        from django.core.files.base import ContentFile

        from apps.tables.qr import render_qr_png

        url = self.get_qr_url()
        digest = hashlib.sha1(url.encode()).hexdigest()[:6]
        filename = f"venue_qr_{self.venue.slug}_{self.number}_{self.code[2:10]}_{digest}.png"
        self.qr_image_url = url
        self.qr_image.save(filename, ContentFile(render_qr_png(url)), save=False)
        self.save(update_fields=["qr_image", "qr_image_url", "updated_at"])

    def regenerate_qr_image(self):
        if self.qr_image:
            self.qr_image.delete(save=False)
        self.generate_qr_image()

    def record_resolve(self):
        self.resolves_count += 1
        self.last_resolved_at = timezone.now()
        self.save(update_fields=["resolves_count", "last_resolved_at"])

    def record_scan(self):
        self.scans_count += 1
        self.last_scanned_at = timezone.now()
        self.save(update_fields=["scans_count", "last_scanned_at"])


class VenueShareRequest(TimeStampedModel):
    """
    "Share tables with us": sent by a manager of one restaurant to another.

    Accepting the first request creates the venue (the acceptor picks whose
    layout seeds it); accepting a request from/to an existing member joins it.
    No token: accept/decline happen inside the target restaurant's own
    authenticated admin/dashboard.
    """

    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_EXPIRED, "Expired"),
    ]
    EXPIRY_DAYS = 14

    from_restaurant = models.ForeignKey(
        "tenants.Restaurant", on_delete=models.CASCADE, related_name="venue_requests_sent"
    )
    to_restaurant = models.ForeignKey(
        "tenants.Restaurant", on_delete=models.CASCADE, related_name="venue_requests_received"
    )
    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    responded_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    venue_name = models.CharField(
        max_length=150, blank=True, help_text=_("Name for the venue if this request creates it")
    )
    message = models.TextField(blank=True)
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "venue_share_requests"
        ordering = ["-created_at"]
        verbose_name = _("Shared venue request")
        verbose_name_plural = _("Shared venue")
        constraints = [
            models.UniqueConstraint(
                fields=["from_restaurant", "to_restaurant"],
                condition=Q(status="pending"),
                name="uniq_pending_share_request",
            )
        ]

    def __str__(self):
        return f"{self.from_restaurant} -> {self.to_restaurant} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(days=self.EXPIRY_DAYS)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_valid(self):
        return self.status == self.STATUS_PENDING and not self.is_expired
