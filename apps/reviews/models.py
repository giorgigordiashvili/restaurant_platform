"""
Models for the reviews app.

Customer-written reviews of a completed order at a specific restaurant.
Reviews can carry up to 5 images and 1 short video, and restaurants /
platform admins can flag them for moderation.
"""

from datetime import timedelta

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel

# One review is tied to one completed order; these grace windows shape
# what the owner can change and what the public sees.
EDIT_WINDOW_DAYS = 7
# Per-review media caps. Enforced at the serializer layer; duplicated
# here so the frontend can import them at build-time once they get
# exposed via drf-spectacular.
MAX_IMAGES_PER_REVIEW = 5
MAX_VIDEOS_PER_REVIEW = 1
MAX_VIDEO_DURATION_S = 30


class Review(TimeStampedModel):
    """A customer review of a completed order."""

    # OneToOne so the DB layer forbids a second review for the same order.
    order = models.OneToOneField(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="review",
    )
    # Denormalised FK for fast per-restaurant listing; kept in sync with
    # order.restaurant at save() time.
    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="reviews",
    )

    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    title = models.CharField(max_length=120, blank=True, default="")
    body = models.TextField(max_length=4000, blank=True, default="")

    # Flipped by the platform-admin moderation queue. Hidden reviews are
    # stripped from public endpoints and excluded from rating aggregates.
    is_hidden = models.BooleanField(default=False, db_index=True)
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "reviews"
        ordering = ["-created_at"]
        verbose_name = "Review"
        verbose_name_plural = "Reviews"
        indexes = [
            models.Index(fields=["restaurant", "is_hidden", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.user.email} · {self.rating}★ · {self.restaurant.name}"

    def save(self, *args, **kwargs):
        # Always keep the denormalised restaurant in sync with the order's
        # restaurant; avoids any drift if an admin ever moves an order.
        if self.order_id and not self.restaurant_id:
            self.restaurant_id = self.order.restaurant_id
        super().save(*args, **kwargs)

    @property
    def edit_locks_at(self):
        return self.created_at + timedelta(days=EDIT_WINDOW_DAYS)

    @property
    def is_editable(self) -> bool:
        return timezone.now() <= self.edit_locks_at


class ReviewMedia(TimeStampedModel):
    """One image or short video attached to a review."""

    IMAGE = "image"
    VIDEO = "video"
    KIND_CHOICES = [(IMAGE, "Image"), (VIDEO, "Video")]

    review = models.ForeignKey(
        Review,
        on_delete=models.CASCADE,
        related_name="media",
    )
    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    file = models.FileField(upload_to="reviews/%Y/%m/")
    # Images: tiny placeholder rendered instantly on the client.
    # Videos: left blank — we show the first-frame poster via the <video>
    # element's native preload.
    blurhash = models.CharField(max_length=64, blank=True, default="")
    duration_s = models.PositiveSmallIntegerField(null=True, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "review_media"
        ordering = ["position", "created_at"]
        verbose_name = "Review Media"
        verbose_name_plural = "Review Media"
        indexes = [models.Index(fields=["review", "position"])]

    def __str__(self):
        return f"{self.kind} for {self.review_id}"


class ReviewReport(TimeStampedModel):
    """A moderation report filed by restaurant staff against a review."""

    REASON_SPAM = "spam"
    REASON_OFFENSIVE = "offensive"
    REASON_NOT_CUSTOMER = "not_a_customer"
    REASON_OFF_TOPIC = "off_topic"
    REASON_OTHER = "other"
    REASON_CHOICES = [
        (REASON_SPAM, "Spam"),
        (REASON_OFFENSIVE, "Offensive"),
        (REASON_NOT_CUSTOMER, "Not a real customer"),
        (REASON_OFF_TOPIC, "Off topic"),
        (REASON_OTHER, "Other"),
    ]

    RESOLUTION_NONE = "none"
    RESOLUTION_KEPT = "kept"
    RESOLUTION_REMOVED = "removed"
    RESOLUTION_CHOICES = [
        (RESOLUTION_NONE, "Open"),
        (RESOLUTION_KEPT, "Kept"),
        (RESOLUTION_REMOVED, "Removed"),
    ]

    review = models.ForeignKey(
        Review,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    reporter = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        related_name="review_reports_filed",
    )
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    notes = models.TextField(blank=True, default="")

    resolution = models.CharField(
        max_length=16,
        choices=RESOLUTION_CHOICES,
        default=RESOLUTION_NONE,
        db_index=True,
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolver = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_reports_resolved",
    )

    class Meta:
        db_table = "review_reports"
        ordering = ["-created_at"]
        verbose_name = "Review Report"
        verbose_name_plural = "Review Reports"
        constraints = [
            models.UniqueConstraint(
                fields=["review", "reporter"],
                name="unique_reporter_per_review",
            )
        ]
        indexes = [models.Index(fields=["resolution", "-created_at"])]

    def __str__(self):
        return f"Report on {self.review_id} ({self.reason})"
