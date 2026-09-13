from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import WaitlistEntry, WaitlistSettings


@admin.register(WaitlistSettings)
class WaitlistSettingsAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["restaurant", "default_wait_minutes", "allow_self_join"]


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["name", "restaurant", "date", "position", "party_size", "status", "created_at"]
    list_filter = ["status", "source"]
    search_fields = ["name", "phone"]
