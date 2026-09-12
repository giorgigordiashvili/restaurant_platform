"""
Delivery platform links stay in ``apps.inventory`` (``RestaurantDeliveryPlatform``,
table ``inventory_delivery_platforms``) because the warehouse checklist and its
adapter registry already import them; this app adds the events / menu syncs
and a proxy that gives the admin page its own sidebar entry.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.inventory.models import RestaurantDeliveryPlatform

PLATFORM_SOURCES = ("glovo", "wolt", "bolt_food")

__all__ = [
    "RestaurantDeliveryPlatform",
    "DeliveryPlatformEvent",
    "PlatformMenuSync",
    "MenuImport",
    "DeliveryPlatformsPage",
    "PLATFORM_SOURCES",
]


class DeliveryPlatformEvent(TimeStampedModel):
    KIND_CHOICES = [
        ("order_created", "Order received"),
        ("order_cancelled", "Order cancelled by platform"),
        ("status_pushed", "Status pushed"),
        ("menu_status", "Menu sync status"),
        ("cancel_requested", "Cancellation requested by restaurant"),
        ("order_status", "Order status notification"),
        ("refund_pushed", "Refund pushed"),
        ("store_status", "Store paused / resumed"),
    ]

    link = models.ForeignKey(RestaurantDeliveryPlatform, on_delete=models.CASCADE, related_name="events")
    event_id = models.CharField(max_length=150, help_text="platform:order_id:kind -- deduplicates retries.")
    kind = models.CharField(max_length=30, choices=KIND_CHOICES)
    payload = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="platform_events"
    )

    class Meta:
        db_table = "delivery_platform_events"
        unique_together = [("link", "event_id")]
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["link", "created_at"])]
        verbose_name = _("Platform event")
        verbose_name_plural = _("Platform events")

    def __str__(self):
        return f"{self.link.platform} {self.kind} {self.event_id}"


class PlatformMenuSync(TimeStampedModel):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sent", "Sent"),
        ("processing", "Processing"),
        ("success", "Success"),
        ("failed", "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    link = models.ForeignKey(RestaurantDeliveryPlatform, on_delete=models.CASCADE, related_name="menu_syncs")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    transaction_id = models.CharField(max_length=100, blank=True, default="")
    product_count = models.PositiveIntegerField(default=0)
    request = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    triggered_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "delivery_menu_syncs"
        ordering = ["-created_at"]
        verbose_name = _("Menu sync")
        verbose_name_plural = _("Menu syncs")

    def __str__(self):
        return f"{self.link.platform} menu sync {self.status}"


class MenuImport(TimeStampedModel):
    """One 'fetch the menu from the platform, preview, import' run."""

    STATUS_CHOICES = [
        ("fetching", "Fetching"),
        ("previewed", "Previewed"),
        ("images", "Importing images"),
        ("imported", "Imported"),
        ("failed", "Failed"),
        ("failed_apply", "Import failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="menu_imports")
    link = models.ForeignKey(
        RestaurantDeliveryPlatform, on_delete=models.SET_NULL, null=True, blank=True, related_name="menu_imports"
    )
    source = models.CharField(max_length=20, choices=RestaurantDeliveryPlatform.PLATFORM_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="fetching")
    options = models.JSONField(default=dict, blank=True)
    raw = models.JSONField(default=dict, blank=True, help_text="The platform's menu payload as received.")
    preview = models.JSONField(default=dict, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    images = models.JSONField(default=list, blank=True, help_text="[[menu_item_id, url], ...] still to download.")
    error = models.TextField(blank=True, default="")
    applied_at = models.DateTimeField(null=True, blank=True)
    triggered_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "delivery_menu_imports"
        ordering = ["-created_at"]
        verbose_name = _("Menu import")
        verbose_name_plural = _("Menu imports")

    def __str__(self):
        return f"{self.source} menu import {self.status}"


class DeliveryPlatformsPage(RestaurantDeliveryPlatform):
    """Proxy: the 'Delivery platforms' page in the tenant admin sidebar."""

    class Meta:
        proxy = True
        app_label = "delivery"
        verbose_name = "Delivery platforms"
        verbose_name_plural = "Delivery platforms"
