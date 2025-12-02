"""
Admin configuration for tables app with multi-tenant support.
"""

from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin, make_active, make_inactive

from .models import Table, TableQRCode, TableSection, TableSession, TableSessionGuest


class TableQRCodeInline(admin.TabularInline):
    model = TableQRCode
    extra = 0
    readonly_fields = ["code", "scans_count", "last_scanned_at"]


@admin.register(TableSection)
class TableSectionAdmin(TenantAwareModelAdmin):
    """Admin for table sections with tenant filtering."""

    tenant_field = "restaurant"

    list_display = ["name", "restaurant", "display_order", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "restaurant__name"]
    ordering = ["restaurant", "display_order"]
    actions = ["export_as_csv", "export_as_json", make_active, make_inactive]


@admin.register(Table)
class TableAdmin(TenantAwareModelAdmin):
    """Admin for tables with tenant filtering."""

    tenant_field = "restaurant"

    list_display = ["number", "name", "restaurant", "section", "capacity", "status", "is_active"]
    list_filter = ["section", "status", "is_active"]
    search_fields = ["number", "name", "restaurant__name"]
    ordering = ["restaurant", "section", "number"]
    inlines = [TableQRCodeInline]
    actions = ["export_as_csv", "export_as_json", make_active, make_inactive]


@admin.register(TableQRCode)
class TableQRCodeAdmin(TenantAwareModelAdmin):
    """Admin for QR codes with tenant filtering."""

    tenant_field = "table__restaurant"

    list_display = ["table", "code", "is_active", "scans_count", "last_scanned_at"]
    list_filter = ["is_active"]
    search_fields = ["code", "table__number"]
    readonly_fields = ["code", "scans_count", "last_scanned_at"]
    actions = ["export_as_csv", "export_as_json", make_active, make_inactive]


@admin.register(TableSession)
class TableSessionAdmin(TenantAwareModelAdmin):
    """Admin for table sessions with tenant filtering."""

    tenant_field = "table__restaurant"

    list_display = ["table", "host", "guest_count", "status", "invite_code", "started_at", "closed_at"]
    list_filter = ["status"]
    search_fields = ["table__number", "invite_code", "host__email"]
    readonly_fields = ["started_at", "invite_code"]
    raw_id_fields = ["host"]


@admin.register(TableSessionGuest)
class TableSessionGuestAdmin(TenantAwareModelAdmin):
    """Admin for session guests with tenant filtering."""

    tenant_field = "session__table__restaurant"

    list_display = ["session", "display_name", "is_host", "status", "joined_at"]
    list_filter = ["is_host", "status"]
    search_fields = ["user__email", "guest_name", "session__invite_code"]
    readonly_fields = ["joined_at", "left_at"]
    raw_id_fields = ["user"]

    def display_name(self, obj):
        return obj.display_name

    display_name.short_description = "Guest"
