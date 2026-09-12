"""Activity feed: read-only audit log for managers (staff:read)."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from unfold.decorators import display

from apps.audit.models import ActivityFeed
from apps.core.tenant_admin_base import TenantModelAdmin


class ActivityFeedTenantAdmin(TenantModelAdmin):
    permission_resource = "staff"
    restaurant_field = "restaurant"
    list_display = ["created_at", "who", "action", "description"]
    list_filter = ["action"]
    search_fields = ["description", "user_email", "user__first_name", "user__last_name"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    readonly_fields = [
        "created_at",
        "user",
        "user_email",
        "action",
        "description",
        "target_model",
        "target_id",
        "changes",
        "ip_address",
    ]
    fields = readonly_fields
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user")

    @display(description=_("Who"))
    def who(self, obj):
        if obj.user_id and obj.user:
            return obj.user.get_full_name() or obj.user.email
        return obj.user_email or _("system")


def register_audit_admin(site):
    site.register(ActivityFeed, ActivityFeedTenantAdmin)
