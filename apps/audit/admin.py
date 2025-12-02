"""
Admin configuration for audit app - read-only, superadmin only.
"""

from django.contrib import admin

from apps.core.admin import SuperadminOnlyMixin, TenantAwareModelAdmin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(SuperadminOnlyMixin, TenantAwareModelAdmin):
    """
    Admin for audit logs.
    - Read-only: No add, change, or delete allowed
    - Superadmin only: Only superusers can view
    - Tenant filtering: Can filter by restaurant
    """

    tenant_field = "restaurant"

    list_display = [
        "action",
        "user_email",
        "ip_address",
        "restaurant",
        "target_model",
        "response_status",
        "created_at",
    ]
    list_filter = [
        "action",
        "response_status",
        "created_at",
    ]
    search_fields = [
        "user_email",
        "ip_address",
        "target_model",
        "target_id",
        "description",
    ]
    readonly_fields = [
        "user",
        "user_email",
        "ip_address",
        "user_agent",
        "restaurant",
        "action",
        "target_model",
        "target_id",
        "description",
        "changes",
        "request_method",
        "request_path",
        "response_status",
        "created_at",
    ]
    ordering = ["-created_at"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
