"""Clock in / out entries and the weekly rota."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class TimeEntry(TimeStampedModel):
    SOURCE_CHOICES = [("pos", "POS"), ("admin", _("Admin")), ("auto", _("Auto-closed"))]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="time_entries")
    staff_member = models.ForeignKey("staff.StaffMember", on_delete=models.CASCADE, related_name="time_entries")
    clock_in = models.DateTimeField(default=timezone.now, db_index=True)
    clock_out = models.DateTimeField(null=True, blank=True)
    break_minutes = models.PositiveSmallIntegerField(default=0)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default="pos")
    note = models.CharField(max_length=200, blank=True, default="")
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    auto_closed = models.BooleanField(default=False, help_text=_("Closed by the system after 16 hours; check it."))

    class Meta:
        db_table = "timekeeping_entries"
        ordering = ["-clock_in"]
        indexes = [models.Index(fields=["restaurant", "clock_in"]), models.Index(fields=["staff_member", "clock_out"])]
        verbose_name = _("Time entry")
        verbose_name_plural = _("Time entries")

    def __str__(self):
        return f"{self.staff_member} {self.clock_in:%d.%m %H:%M}–{self.clock_out:%H:%M if self.clock_out else '…'}"

    @property
    def is_open(self) -> bool:
        return self.clock_out is None

    @property
    def worked_minutes(self) -> int:
        end = self.clock_out or timezone.now()
        raw = int((end - self.clock_in).total_seconds() // 60) - int(self.break_minutes or 0)
        return max(raw, 0)

    @property
    def worked_hours(self) -> Decimal:
        return (Decimal(self.worked_minutes) / Decimal(60)).quantize(Decimal("0.01"))


class RotaShift(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    restaurant = models.ForeignKey("tenants.Restaurant", on_delete=models.CASCADE, related_name="rota_shifts")
    staff_member = models.ForeignKey("staff.StaffMember", on_delete=models.CASCADE, related_name="rota_shifts")
    date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField(help_text=_("Earlier than the start = the shift ends after midnight."))
    label = models.CharField(max_length=60, blank=True, default="", help_text=_("E.g. 'Bar', 'Kitchen', 'Floor'."))
    note = models.CharField(max_length=200, blank=True, default="")
    published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "timekeeping_rota_shifts"
        ordering = ["date", "start_time"]
        indexes = [models.Index(fields=["restaurant", "date"])]
        verbose_name = _("Rota shift")
        verbose_name_plural = _("Rota")

    def __str__(self):
        return f"{self.staff_member} {self.date:%d.%m} {self.start_time:%H:%M}–{self.end_time:%H:%M}"

    @staticmethod
    def _t(value):
        if isinstance(value, str):
            return datetime.strptime(value[:8], "%H:%M:%S" if value.count(":") == 2 else "%H:%M").time()
        return value

    @property
    def hours(self) -> Decimal:
        start = datetime.combine(self.date, self._t(self.start_time))
        end = datetime.combine(self.date, self._t(self.end_time))
        if end <= start:
            end += timedelta(days=1)
        return (Decimal((end - start).total_seconds()) / Decimal(3600)).quantize(Decimal("0.01"))

    def starts_at(self, tz):
        return datetime.combine(self.date, self._t(self.start_time), tzinfo=tz)
