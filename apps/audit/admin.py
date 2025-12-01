from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Admin for audit logs."""

    list_display = [
        "action",
        "user_email",
        "ip_address",
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

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
