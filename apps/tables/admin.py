"""
Admin configuration for tables app.
"""

from django.contrib import admin

from .models import Table, TableQRCode, TableSection, TableSession


class TableQRCodeInline(admin.TabularInline):
    model = TableQRCode
    extra = 0
    readonly_fields = ["code", "scans_count", "last_scanned_at"]


@admin.register(TableSection)
class TableSectionAdmin(admin.ModelAdmin):
    list_display = ["name", "restaurant", "display_order", "is_active"]
    list_filter = ["restaurant", "is_active"]
    search_fields = ["name", "restaurant__name"]
    ordering = ["restaurant", "display_order"]


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ["number", "name", "restaurant", "section", "capacity", "status", "is_active"]
    list_filter = ["restaurant", "section", "status", "is_active"]
    search_fields = ["number", "name", "restaurant__name"]
    ordering = ["restaurant", "section", "number"]
    inlines = [TableQRCodeInline]


@admin.register(TableQRCode)
class TableQRCodeAdmin(admin.ModelAdmin):
    list_display = ["table", "code", "is_active", "scans_count", "last_scanned_at"]
    list_filter = ["table__restaurant", "is_active"]
    search_fields = ["code", "table__number"]
    readonly_fields = ["code", "scans_count", "last_scanned_at"]


@admin.register(TableSession)
class TableSessionAdmin(admin.ModelAdmin):
    list_display = ["table", "guest_count", "status", "started_at", "closed_at"]
    list_filter = ["table__restaurant", "status"]
    search_fields = ["table__number"]
    readonly_fields = ["started_at"]
