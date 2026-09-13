"""
Online ordering from the restaurant's own page: pickup / delivery settings,
delivery zones with fees, staff couriers, one ``Delivery`` per delivery order
(own courier or a hand-off to Wolt Drive / Glovo On-Demand) and custom domains.
"""

from __future__ import annotations

import secrets
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

COURIER_PROVIDER_CHOICES = [
    ("own", _("Own couriers")),
    ("wolt_drive", _("Wolt Drive")),
    ("glovo_odr", _("Glovo On-Demand")),
]


class OnlineOrderingSettings(TimeStampedModel):
    AUTO_REQUEST_CHOICES = [
        ("confirmed", _("When the order is accepted")),
        ("ready", _("When the order is ready")),
        ("manual", _("Manually from the POS")),
    ]

    restaurant = models.OneToOneField("tenants.Restaurant", on_delete=models.CASCADE, related_name="ordering_settings")
    pickup_enabled = models.BooleanField(default=True, help_text=_("Guests can order for pickup."))
    delivery_enabled = models.BooleanField(default=False, help_text=_("Guests can order delivery (needs zones)."))
    asap_enabled = models.BooleanField(default=True, help_text=_("Allow 'as soon as possible' orders."))
    scheduling_enabled = models.BooleanField(default=True, help_text=_("Allow choosing a later time slot."))
    lead_minutes = models.PositiveSmallIntegerField(default=30, help_text=_("Preparation time promised to guests."))
    delivery_extra_minutes = models.PositiveSmallIntegerField(
        default=20, help_text=_("Added to the lead time for delivery orders.")
    )
    slot_interval_minutes = models.PositiveSmallIntegerField(default=15)
    max_days_ahead = models.PositiveSmallIntegerField(default=3, validators=[MaxValueValidator(30)])
    cutoff_minutes_before_close = models.PositiveSmallIntegerField(
        default=30, help_text=_("Stop taking online orders this long before closing.")
    )
    min_order_pickup = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    min_order_delivery = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    free_delivery_over = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text=_("0 = never free."))
    packaging_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text=_("Per order."))
    courier_provider = models.CharField(max_length=20, choices=COURIER_PROVIDER_CHOICES, default="own")
    auto_request_courier_on = models.CharField(max_length=20, choices=AUTO_REQUEST_CHOICES, default="ready")
    pass_platform_fee_to_guest = models.BooleanField(
        default=False, help_text=_("Charge the guest what Wolt / Glovo quote instead of the zone fee.")
    )
    paused_until = models.DateTimeField(
        null=True, blank=True, help_text=_("Online orders paused until then (kitchen slammed).")
    )
    pause_reason = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        db_table = "ordering_settings"
        verbose_name = _("Online ordering settings")
        verbose_name_plural = _("Online ordering settings")

    def __str__(self):
        return f"Online ordering: {self.restaurant}"


class DeliveryZone(TimeStampedModel):
    KIND_CHOICES = [("radius", _("Radius")), ("polygon", _("Polygon"))]

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="delivery_zones")
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="radius")
    radius_km = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("3"), validators=[MinValueValidator(Decimal("0.1"))]
    )
    polygon = models.JSONField(default=list, blank=True, help_text=_("[[lat, lng], ...] for polygon zones."))
    fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    min_order = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    eta_minutes = models.PositiveSmallIntegerField(default=45)
    is_active = models.BooleanField(default=True)
    sort = models.PositiveSmallIntegerField(default=0, help_text=_("Lower = checked first (inner ring first)."))
    color = models.CharField(max_length=7, blank=True, default="")

    class Meta:
        db_table = "ordering_delivery_zones"
        ordering = ["sort", "created_at"]
        verbose_name = _("Delivery zone")
        verbose_name_plural = _("Delivery zones")

    def __str__(self):
        return self.name

    def contains(self, lat, lng) -> bool:
        from apps.ordering.geo import distance_km, point_in_polygon, valid_coords

        if not valid_coords(lat, lng):
            return False
        if self.kind == "polygon":
            return point_in_polygon(lat, lng, self.polygon)
        r = self.restaurant
        if r.latitude is None or r.longitude is None:
            return False
        return distance_km(r.latitude, r.longitude, lat, lng) <= float(self.radius_km)


class Courier(TimeStampedModel):
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="couriers")
    staff = models.ForeignKey(
        "staff.StaffMember", on_delete=models.SET_NULL, null=True, blank=True, related_name="courier_profiles"
    )
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True, default="")
    vehicle = models.CharField(max_length=40, blank=True, default="", help_text=_("bike / scooter / car"))
    is_active = models.BooleanField(default=True)
    is_available = models.BooleanField(default=True, help_text=_("On shift and taking deliveries."))

    class Meta:
        db_table = "ordering_couriers"
        ordering = ["name"]
        verbose_name = _("Courier")
        verbose_name_plural = _("Couriers")

    def __str__(self):
        return self.name


class Delivery(TimeStampedModel):
    STATUS_CHOICES = [
        ("pending", _("Not requested")),
        ("quoted", _("Quoted")),
        ("requested", _("Requested")),
        ("accepted", _("Accepted by platform")),
        ("assigned", _("Courier assigned")),
        ("picked_up", _("Picked up")),
        ("delivered", _("Delivered")),
        ("failed", _("Failed")),
        ("cancelled", _("Cancelled")),
    ]
    OPEN = ("quoted", "requested", "accepted", "assigned", "picked_up")
    FINAL = ("delivered", "failed", "cancelled")

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="deliveries")
    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="delivery")
    provider = models.CharField(max_length=20, choices=COURIER_PROVIDER_CHOICES, default="own")
    courier = models.ForeignKey(Courier, on_delete=models.SET_NULL, null=True, blank=True, related_name="deliveries")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    external_id = models.CharField(max_length=120, blank=True, default="", db_index=True)
    tracking_url = models.URLField(max_length=500, blank=True, default="")
    quote = models.JSONField(default=dict, blank=True)
    cost = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, help_text=_("What the courier platform charges us.")
    )
    fee_charged = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, help_text=_("Delivery fee the guest paid.")
    )
    pickup_eta = models.DateTimeField(null=True, blank=True)
    dropoff_eta = models.DateTimeField(null=True, blank=True)
    courier_name = models.CharField(max_length=120, blank=True, default="")
    courier_phone = models.CharField(max_length=30, blank=True, default="")
    courier_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    courier_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    events = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True, default="")
    requested_at = models.DateTimeField(null=True, blank=True)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "ordering_deliveries"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["restaurant", "status", "created_at"])]
        verbose_name = _("Delivery")
        verbose_name_plural = _("Deliveries")

    def __str__(self):
        return f"{self.order.order_number} · {self.get_provider_display()} · {self.get_status_display()}"

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN

    def log(self, kind: str, **payload) -> None:
        from django.utils import timezone

        self.events = [*(self.events or []), {"at": timezone.now().isoformat(), "kind": kind, **payload}][-50:]


class DeliveryEvent(TimeStampedModel):
    """Raw courier-platform webhooks, keyed for idempotency."""

    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="webhook_events")
    event_id = models.CharField(max_length=150)
    kind = models.CharField(max_length=60, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ordering_delivery_events"
        unique_together = [("delivery", "event_id")]
        ordering = ["-created_at"]


def _domain_token() -> str:
    return secrets.token_urlsafe(12)


class RestaurantDomain(TimeStampedModel):
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="domains")
    domain = models.CharField(max_length=253, unique=True, help_text=_("e.g. order.myrestaurant.ge"))
    is_primary = models.BooleanField(default=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_check_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=200, blank=True, default="")
    verification_token = models.CharField(max_length=40, default=_domain_token, editable=False)

    class Meta:
        db_table = "ordering_domains"
        ordering = ["-is_primary", "domain"]
        verbose_name = _("Custom domain")
        verbose_name_plural = _("Custom domains")

    def __str__(self):
        return self.domain

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None

    def save(self, *args, **kwargs):
        self.domain = (self.domain or "").strip().lower().rstrip(".")
        super().save(*args, **kwargs)


class OnlineOrderingSettingsPage(OnlineOrderingSettings):
    """Proxy: the single-form settings page in the tenant admin."""

    class Meta:
        proxy = True
        verbose_name = _("Online ordering")
        verbose_name_plural = _("Online ordering")
