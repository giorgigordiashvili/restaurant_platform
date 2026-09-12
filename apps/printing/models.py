"""
Printers and print jobs.

A ``Printer`` is a destination (kitchen, bar or receipt) reached through a
print bridge -- a small program on any machine next to the printer that
polls for jobs with its ``bridge_key`` and pushes the bytes to USB / LAN /
Bluetooth. ``PrintJob`` carries the fully rendered ESC/POS bytes, so the
bridge needs no fonts and no knowledge of the menu.
"""

from __future__ import annotations

import secrets

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

STATIONS = ("kitchen", "bar")


def new_bridge_key() -> str:
    return secrets.token_urlsafe(24)


class Printer(TimeStampedModel):
    KIND_CHOICES = [("kitchen", "Kitchen tickets"), ("bar", "Bar tickets"), ("receipt", "Receipts")]
    STATION_CHOICES = [("kitchen", "Kitchen"), ("bar", "Bar"), ("both", "Kitchen + bar")]
    PAPER_CHOICES = [("80", "80 mm"), ("58", "58 mm")]
    CONNECTION_CHOICES = [("bridge", "Print bridge (USB / LAN / Bluetooth)"), ("browser", "Browser print (no bridge)")]

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="printers")
    name = models.CharField(max_length=100, help_text="E.g. 'Kitchen pass', 'Bar', 'Till'.")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="kitchen")
    stations = models.CharField(
        max_length=10,
        choices=STATION_CHOICES,
        default="kitchen",
        help_text="Which items go on this printer's tickets (kitchen printers only).",
    )
    paper = models.CharField(max_length=2, choices=PAPER_CHOICES, default="80")
    connection = models.CharField(max_length=10, choices=CONNECTION_CHOICES, default="bridge")
    bridge_key = models.CharField(max_length=64, unique=True, default=new_bridge_key, editable=False)
    copies = models.PositiveSmallIntegerField(default=1)
    auto_print = models.BooleanField(
        default=True,
        help_text="Kitchen/bar: print a ticket when an order is accepted. Receipt: print when a payment is taken.",
    )
    open_drawer = models.BooleanField(
        default=False, help_text="Receipt printers: kick the cash drawer on cash payments."
    )
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, help_text="Last poll from the bridge.")
    last_error = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = "printers"
        ordering = ["kind", "name"]
        verbose_name = _("Printer")
        verbose_name_plural = _("Printers")

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"

    @property
    def is_online(self) -> bool:
        return bool(self.last_seen_at) and timezone.now() - self.last_seen_at < timezone.timedelta(minutes=2)

    def serves(self, station: str) -> bool:
        """Does a kitchen/bar printer take items of this preparation station?"""
        if self.kind == "receipt":
            return False
        if station == "both":
            return True
        return self.stations in ("both", station)

    def rotate_key(self):
        self.bridge_key = new_bridge_key()
        self.save(update_fields=["bridge_key", "updated_at"])


class PrintJob(TimeStampedModel):
    KIND_CHOICES = [("ticket", "Kitchen ticket"), ("receipt", "Receipt"), ("report", "Report"), ("test", "Test page")]
    STATUS_CHOICES = [("queued", "Queued"), ("printing", "Printing"), ("done", "Done"), ("failed", "Failed")]
    MAX_ATTEMPTS = 3

    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="print_jobs")
    printer = models.ForeignKey(Printer, on_delete=models.CASCADE, related_name="jobs")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="print_jobs"
    )
    payment = models.ForeignKey(
        "payments.Payment", on_delete=models.SET_NULL, null=True, blank=True, related_name="print_jobs"
    )
    title = models.CharField(max_length=120, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True, help_text="What was rendered (for reprints / audit).")
    escpos = models.BinaryField(help_text="Rendered ESC/POS bytes the bridge writes to the device.")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="queued", db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True, default="")
    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="print_jobs"
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    printed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "print_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["printer", "status", "created_at"]),
            models.Index(fields=["restaurant", "status"]),
        ]
        verbose_name = _("Print job")
        verbose_name_plural = _("Print jobs")

    def __str__(self):
        return f"{self.get_kind_display()} · {self.title or self.pk} ({self.status})"
