"""
Tenant (Restaurant) admin configuration with superadmin features.
"""

from django.contrib import admin

from apps.core.admin import ExportMixin, SuperadminOnlyMixin, make_active, make_inactive

from .models import Amenity, Restaurant, RestaurantCategory, RestaurantHours


@admin.register(RestaurantCategory)
class RestaurantCategoryAdmin(SuperadminOnlyMixin, admin.ModelAdmin):
    """Admin for restaurant categories - superadmin only."""

    list_display = ["name", "slug", "icon", "display_order", "is_active", "restaurants_count"]
    list_filter = ["is_active"]
    search_fields = ["name", "description"]
    prepopulated_fields = {"slug": ("name",)}
    list_editable = ["display_order", "is_active"]
    ordering = ["display_order", "name"]

    @admin.display(description="Restaurants")
    def restaurants_count(self, obj):
        return obj.restaurants_count


@admin.register(Amenity)
class AmenityAdmin(SuperadminOnlyMixin, admin.ModelAdmin):
    """Admin for amenities - superadmin only."""

    list_display = ["name", "slug", "icon", "display_order", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "description"]
    prepopulated_fields = {"slug": ("name",)}
    list_editable = ["display_order", "is_active"]
    ordering = ["display_order", "name"]


class RestaurantHoursInline(admin.TabularInline):
    model = RestaurantHours
    extra = 0
    max_num = 7
    ordering = ["day_of_week"]


@admin.register(Restaurant)
class RestaurantAdmin(SuperadminOnlyMixin, ExportMixin, admin.ModelAdmin):
    """
    Admin for restaurants - superadmin only.
    This is the root tenant model, so no tenant_field needed.
    """

    list_display = ["name", "slug", "category", "city", "is_active", "owner", "average_rating", "total_orders", "created_at"]
    list_filter = ["is_active", "category", "city", "country", "default_currency", "created_at"]
    search_fields = ["name", "slug", "email", "phone", "city", "owner__email"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["created_at", "updated_at", "average_rating", "total_reviews", "total_orders"]
    raw_id_fields = ["owner"]
    inlines = [RestaurantHoursInline]
    actions = ["export_as_csv", "export_as_json", make_active, make_inactive]

    filter_horizontal = ["amenities"]

    fieldsets = (
        (None, {"fields": ("name", "slug", "description", "category", "is_active", "owner")}),
        ("Amenities", {"fields": ("amenities",)}),
        ("Contact", {"fields": ("email", "phone", "website")}),
        ("Address", {"fields": ("address", "city", "postal_code", "country", "latitude", "longitude")}),
        (
            "Branding",
            {
                "fields": ("logo", "cover_image", "primary_color", "secondary_color"),
                "classes": ("collapse",),
            },
        ),
        (
            "Settings",
            {
                "fields": (
                    "default_currency",
                    "timezone",
                    "default_language",
                    "tax_rate",
                    "service_charge",
                    "minimum_order_amount",
                    "average_preparation_time",
                )
            },
        ),
        ("Features", {"fields": ("accepts_remote_orders", "accepts_reservations", "accepts_takeaway")}),
        (
            "Statistics",
            {
                "fields": ("average_rating", "total_reviews", "total_orders"),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(RestaurantHours)
class RestaurantHoursAdmin(SuperadminOnlyMixin, admin.ModelAdmin):
    """Admin for restaurant hours - superadmin only."""

    list_display = ["restaurant", "day_of_week", "open_time", "close_time", "is_closed"]
    list_filter = ["restaurant", "day_of_week", "is_closed"]
    search_fields = ["restaurant__name"]
    ordering = ["restaurant", "day_of_week"]
