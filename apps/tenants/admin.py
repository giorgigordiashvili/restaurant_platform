"""
Tenant (Restaurant) admin configuration.
"""

from django.contrib import admin

from .models import Restaurant, RestaurantHours


class RestaurantHoursInline(admin.TabularInline):
    model = RestaurantHours
    extra = 0
    max_num = 7
    ordering = ["day_of_week"]


@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "city", "is_active", "owner", "average_rating", "created_at"]
    list_filter = ["is_active", "city", "country", "default_currency", "created_at"]
    search_fields = ["name", "slug", "email", "phone", "city"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["created_at", "updated_at", "average_rating", "total_reviews", "total_orders"]
    raw_id_fields = ["owner"]
    inlines = [RestaurantHoursInline]

    fieldsets = (
        (None, {
            "fields": ("name", "slug", "description", "is_active", "owner")
        }),
        ("Contact", {
            "fields": ("email", "phone", "website")
        }),
        ("Address", {
            "fields": ("address", "city", "postal_code", "country", "latitude", "longitude")
        }),
        ("Branding", {
            "fields": ("logo", "cover_image", "primary_color", "secondary_color"),
            "classes": ("collapse",),
        }),
        ("Settings", {
            "fields": (
                "default_currency",
                "timezone",
                "default_language",
                "tax_rate",
                "service_charge",
                "minimum_order_amount",
                "average_preparation_time",
            )
        }),
        ("Features", {
            "fields": ("accepts_remote_orders", "accepts_reservations", "accepts_takeaway")
        }),
        ("Statistics", {
            "fields": ("average_rating", "total_reviews", "total_orders"),
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )


@admin.register(RestaurantHours)
class RestaurantHoursAdmin(admin.ModelAdmin):
    list_display = ["restaurant", "day_of_week", "open_time", "close_time", "is_closed"]
    list_filter = ["restaurant", "day_of_week", "is_closed"]
    ordering = ["restaurant", "day_of_week"]
