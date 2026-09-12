"""Platform (superadmin) admin for venues."""

from django.contrib import admin

from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import TabularInline as UnfoldTabularInline

from apps.core.admin import SuperadminOnlyMixin

from .models import Venue, VenueMember, VenueSection, VenueShareRequest, VenueTable


class VenueMemberInline(UnfoldTabularInline):
    model = VenueMember
    extra = 0
    autocomplete_fields = ["restaurant"]
    fields = ["restaurant", "display_order", "is_layout_seed", "joined_at"]
    readonly_fields = ["joined_at"]


class VenueSectionInline(UnfoldTabularInline):
    model = VenueSection
    extra = 0
    fields = ["name", "display_order", "is_active"]


class VenueTableInline(UnfoldTabularInline):
    model = VenueTable
    extra = 0
    fields = ["number", "name", "section", "capacity", "min_capacity", "shape", "is_active", "code"]
    readonly_fields = ["code"]


@admin.register(Venue)
class VenueAdmin(SuperadminOnlyMixin, UnfoldModelAdmin):
    list_display = ["name", "slug", "is_active", "members_count", "tables_count", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug", "memberships__restaurant__name"]
    readonly_fields = ["slug", "created_by", "created_at", "updated_at"]
    inlines = [VenueMemberInline, VenueSectionInline, VenueTableInline]

    @admin.display(description="Members")
    def members_count(self, obj):
        return obj.memberships.count()

    @admin.display(description="Tables")
    def tables_count(self, obj):
        return obj.tables.filter(is_active=True).count()


@admin.register(VenueShareRequest)
class VenueShareRequestAdmin(SuperadminOnlyMixin, UnfoldModelAdmin):
    list_display = ["from_restaurant", "to_restaurant", "status", "venue_name", "created_at", "expires_at"]
    list_filter = ["status"]
    search_fields = ["from_restaurant__name", "to_restaurant__name", "venue_name"]
    readonly_fields = ["requested_by", "responded_by", "responded_at", "created_at", "updated_at"]
    autocomplete_fields = ["from_restaurant", "to_restaurant"]
