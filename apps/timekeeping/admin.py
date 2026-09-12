from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import RotaShift, TimeEntry


@admin.register(TimeEntry)
class TimeEntryAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["staff_member", "restaurant", "clock_in", "clock_out", "auto_closed"]


@admin.register(RotaShift)
class RotaShiftAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["staff_member", "restaurant", "date", "start_time", "end_time", "published"]
