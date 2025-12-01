"""
Staff admin configuration.
"""

from django.contrib import admin

from .models import StaffInvitation, StaffMember, StaffRole


@admin.register(StaffRole)
class StaffRoleAdmin(admin.ModelAdmin):
    list_display = ["name", "restaurant", "is_system_role", "created_at"]
    list_filter = ["name", "is_system_role", "restaurant"]
    search_fields = ["name", "display_name", "restaurant__name"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["restaurant", "name"]


@admin.register(StaffMember)
class StaffMemberAdmin(admin.ModelAdmin):
    list_display = ["user", "restaurant", "role", "is_active", "joined_at"]
    list_filter = ["is_active", "role__name", "restaurant"]
    search_fields = ["user__email", "user__first_name", "user__last_name", "restaurant__name"]
    readonly_fields = ["created_at", "updated_at", "invited_at", "joined_at"]
    raw_id_fields = ["user", "invited_by"]
    ordering = ["-created_at"]


@admin.register(StaffInvitation)
class StaffInvitationAdmin(admin.ModelAdmin):
    list_display = ["email", "restaurant", "role", "status", "expires_at", "created_at"]
    list_filter = ["status", "role__name", "restaurant"]
    search_fields = ["email", "restaurant__name"]
    readonly_fields = ["token", "created_at", "updated_at", "accepted_at"]
    raw_id_fields = ["invited_by", "accepted_by"]
    ordering = ["-created_at"]
