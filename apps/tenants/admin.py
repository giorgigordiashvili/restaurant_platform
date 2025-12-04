"""
Tenant (Restaurant) admin configuration with superadmin features.
"""

from django.contrib import admin

from parler.admin import TranslatableAdmin

from apps.core.admin import ExportMixin, SuperadminOnlyMixin, make_active, make_inactive

from .models import Amenity, Restaurant, RestaurantCategory, RestaurantHours


# Unfold input styling classes for superadmin
UNFOLD_INPUT_CLASSES = (
    "border border-base-200 bg-white font-medium min-w-20 placeholder-base-400 "
    "rounded-default shadow-xs text-font-default-light text-sm focus:outline-2 "
    "focus:-outline-offset-2 focus:outline-primary-600 dark:bg-base-900 "
    "dark:border-base-700 dark:text-font-default-dark dark:focus:outline-primary-500 "
    "px-3 py-2 w-full max-w-2xl"
)

UNFOLD_TEXTAREA_CLASSES = (
    "border border-base-200 bg-white font-medium placeholder-base-400 "
    "rounded-default shadow-xs text-font-default-light text-sm focus:outline-2 "
    "focus:-outline-offset-2 focus:outline-primary-600 dark:bg-base-900 "
    "dark:border-base-700 dark:text-font-default-dark dark:focus:outline-primary-500 "
    "px-3 py-2 w-full max-w-2xl min-h-[120px]"
)


class StyledTranslatableAdmin(SuperadminOnlyMixin, TranslatableAdmin):
    """
    TranslatableAdmin with Unfold styling for superadmin.
    Applies consistent input styling to parler translation fields.
    """

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Apply Unfold styling to all form fields
        for field_name, field in form.base_fields.items():
            widget = field.widget
            existing_classes = widget.attrs.get("class", "")
            if "Textarea" in widget.__class__.__name__:
                widget.attrs["class"] = f"{existing_classes} {UNFOLD_TEXTAREA_CLASSES}".strip()
            else:
                widget.attrs["class"] = f"{existing_classes} {UNFOLD_INPUT_CLASSES}".strip()
        return form


@admin.register(RestaurantCategory)
class RestaurantCategoryAdmin(StyledTranslatableAdmin):
    """Admin for restaurant categories - superadmin only with translations."""

    list_display = ["name", "slug", "icon", "display_order", "is_active", "restaurants_count"]
    list_filter = ["is_active"]
    search_fields = ["translations__name", "translations__description"]
    list_editable = ["display_order", "is_active"]
    ordering = ["display_order"]

    fieldsets = (
        (None, {"fields": ("slug", "icon", "image")}),
        ("Settings", {"fields": ("display_order", "is_active")}),
    )

    def name(self, obj):
        return obj.safe_translation_getter("name", default="-")
    name.short_description = "Name"

    @admin.display(description="Restaurants")
    def restaurants_count(self, obj):
        return obj.restaurants_count


@admin.register(Amenity)
class AmenityAdmin(StyledTranslatableAdmin):
    """Admin for amenities - superadmin only with translations."""

    list_display = ["name", "slug", "icon", "display_order", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["translations__name", "translations__description"]
    list_editable = ["display_order", "is_active"]
    ordering = ["display_order"]

    fieldsets = (
        (None, {"fields": ("slug", "icon")}),
        ("Settings", {"fields": ("display_order", "is_active")}),
    )

    def name(self, obj):
        return obj.safe_translation_getter("name", default="-")
    name.short_description = "Name"


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
