from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import Automation, Campaign, Customer, Segment


@admin.register(Customer)
class CustomerAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["name", "phone", "restaurant", "visits", "total_spend", "marketing_opt_in", "last_visit_at"]
    search_fields = ["name", "phone", "email"]


@admin.register(Segment)
class SegmentAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["name", "restaurant", "is_builtin", "is_active"]


@admin.register(Campaign)
class CampaignAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["name", "restaurant", "channel", "status", "sent_count"]


@admin.register(Automation)
class AutomationAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["kind", "restaurant", "enabled", "sent_count"]
