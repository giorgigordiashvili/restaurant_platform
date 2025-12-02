"""
Admin configuration for reservations with multi-tenant support.
"""

from django.contrib import admin
from django.utils.html import format_html

from apps.core.admin import ReadOnlyAdminMixin, TenantAwareModelAdmin

from .models import (
    Reservation,
    ReservationBlockedTime,
    ReservationHistory,
    ReservationSettings,
)


class ReservationHistoryInline(admin.TabularInline):
    """Inline for reservation history."""

    model = ReservationHistory
    extra = 0
    readonly_fields = ["created_at", "previous_status", "new_status", "changed_by", "notes"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ReservationSettings)
class ReservationSettingsAdmin(TenantAwareModelAdmin):
    """Admin for reservation settings with tenant filtering."""

    tenant_field = "restaurant"

    list_display = [
        "restaurant",
        "accepts_reservations",
        "min_party_size",
        "max_party_size",
        "advance_booking_days",
        "require_confirmation",
    ]
    list_filter = ["accepts_reservations", "require_confirmation"]
    search_fields = ["restaurant__name"]

    fieldsets = (
        (
            None,
            {
                "fields": ("restaurant", "accepts_reservations"),
            },
        ),
        (
            "Party Size",
            {
                "fields": ("min_party_size", "max_party_size", "reservation_duration"),
            },
        ),
        (
            "Booking Window",
            {
                "fields": (
                    "advance_booking_days",
                    "min_advance_hours",
                    "buffer_minutes",
                    "slot_interval_minutes",
                ),
            },
        ),
        (
            "Confirmation & Cancellation",
            {
                "fields": (
                    "require_confirmation",
                    "auto_confirm_threshold",
                    "cancellation_deadline_hours",
                ),
            },
        ),
        (
            "Notifications",
            {
                "fields": ("send_reminder", "reminder_hours_before"),
            },
        ),
        (
            "Capacity Limits",
            {
                "fields": ("max_daily_reservations", "max_hourly_reservations"),
            },
        ),
    )


@admin.register(Reservation)
class ReservationAdmin(TenantAwareModelAdmin):
    """Admin for reservations with tenant filtering."""

    tenant_field = "restaurant"

    list_display = [
        "confirmation_code",
        "guest_name",
        "restaurant",
        "reservation_date",
        "reservation_time",
        "party_size",
        "status_badge",
        "table",
        "source",
    ]
    list_filter = [
        "status",
        "source",
        "reservation_date",
    ]
    search_fields = [
        "confirmation_code",
        "guest_name",
        "guest_email",
        "guest_phone",
        "customer__email",
    ]
    date_hierarchy = "reservation_date"
    ordering = ["-reservation_date", "-reservation_time"]
    readonly_fields = [
        "confirmation_code",
        "confirmed_at",
        "cancelled_at",
        "seated_at",
        "completed_at",
        "reminder_sent_at",
        "created_at",
        "updated_at",
    ]
    autocomplete_fields = ["restaurant", "customer", "table", "confirmed_by", "cancelled_by"]
    inlines = [ReservationHistoryInline]

    fieldsets = (
        (
            "Guest Information",
            {
                "fields": ("customer", "guest_name", "guest_email", "guest_phone"),
            },
        ),
        (
            "Reservation Details",
            {
                "fields": (
                    "restaurant",
                    "reservation_date",
                    "reservation_time",
                    "party_size",
                    "duration",
                    "table",
                ),
            },
        ),
        (
            "Status",
            {
                "fields": ("status", "source", "confirmation_code"),
            },
        ),
        (
            "Notes",
            {
                "fields": ("special_requests", "internal_notes"),
            },
        ),
        (
            "Confirmation",
            {
                "fields": ("confirmed_at", "confirmed_by"),
                "classes": ("collapse",),
            },
        ),
        (
            "Cancellation",
            {
                "fields": ("cancelled_at", "cancelled_by", "cancellation_reason"),
                "classes": ("collapse",),
            },
        ),
        (
            "Timeline",
            {
                "fields": ("seated_at", "completed_at"),
                "classes": ("collapse",),
            },
        ),
        (
            "Reminders",
            {
                "fields": ("reminder_sent", "reminder_sent_at"),
                "classes": ("collapse",),
            },
        ),
        (
            "Metadata",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def status_badge(self, obj):
        """Display status as colored badge."""
        colors = {
            "pending": "#f0ad4e",
            "confirmed": "#5cb85c",
            "waitlist": "#5bc0de",
            "seated": "#337ab7",
            "completed": "#777",
            "cancelled": "#d9534f",
            "no_show": "#292b2c",
        }
        color = colors.get(obj.status, "#777")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; '
            'border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display(),
        )

    status_badge.short_description = "Status"

    actions = ["export_as_csv", "export_as_json", "confirm_reservations", "cancel_reservations", "mark_as_no_show"]

    @admin.action(description="Confirm selected reservations")
    def confirm_reservations(self, request, queryset):
        updated = queryset.filter(status="pending").update(status="confirmed")
        self.message_user(request, f"{updated} reservation(s) confirmed.")

    @admin.action(description="Cancel selected reservations")
    def cancel_reservations(self, request, queryset):
        updated = queryset.exclude(status__in=["cancelled", "completed"]).update(status="cancelled")
        self.message_user(request, f"{updated} reservation(s) cancelled.")

    @admin.action(description="Mark selected as no-show")
    def mark_as_no_show(self, request, queryset):
        updated = queryset.filter(status__in=["pending", "confirmed"]).update(status="no_show")
        self.message_user(request, f"{updated} reservation(s) marked as no-show.")


@admin.register(ReservationBlockedTime)
class ReservationBlockedTimeAdmin(TenantAwareModelAdmin):
    """Admin for blocked times with tenant filtering."""

    tenant_field = "restaurant"

    list_display = [
        "restaurant",
        "start_datetime",
        "end_datetime",
        "reason",
        "is_recurring",
        "created_by",
    ]
    list_filter = ["reason", "is_recurring"]
    search_fields = ["restaurant__name", "description"]
    date_hierarchy = "start_datetime"
    autocomplete_fields = ["restaurant", "created_by"]
    filter_horizontal = ["tables"]

    fieldsets = (
        (
            None,
            {
                "fields": ("restaurant", "reason", "description"),
            },
        ),
        (
            "Time Period",
            {
                "fields": ("start_datetime", "end_datetime"),
            },
        ),
        (
            "Tables",
            {
                "fields": ("tables",),
                "description": "Leave empty to block all tables",
            },
        ),
        (
            "Recurrence",
            {
                "fields": ("is_recurring", "recurrence_pattern"),
                "classes": ("collapse",),
            },
        ),
        (
            "Metadata",
            {
                "fields": ("created_by",),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(ReservationHistory)
class ReservationHistoryAdmin(ReadOnlyAdminMixin, TenantAwareModelAdmin):
    """Admin for reservation history - read-only with tenant filtering."""

    tenant_field = "reservation__restaurant"

    list_display = [
        "reservation",
        "previous_status",
        "new_status",
        "changed_by",
        "created_at",
    ]
    list_filter = ["new_status", "created_at"]
    search_fields = ["reservation__confirmation_code", "reservation__guest_name"]
    readonly_fields = ["reservation", "previous_status", "new_status", "changed_by", "notes", "created_at"]
    date_hierarchy = "created_at"
