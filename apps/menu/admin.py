"""
Menu admin configuration with translation support and multi-tenant awareness.
"""

from django.contrib import admin

from parler.admin import TranslatableTabularInline

from apps.core.admin import TenantAwareModelAdmin, TenantAwareTranslatableAdmin, make_active, make_inactive

from .models import MenuCategory, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup


class ModifierInline(TranslatableTabularInline):
    model = Modifier
    extra = 1
    ordering = ["display_order"]


class MenuItemModifierGroupInline(admin.TabularInline):
    model = MenuItemModifierGroup
    extra = 1
    ordering = ["display_order"]
    autocomplete_fields = ["modifier_group"]


@admin.register(MenuCategory)
class MenuCategoryAdmin(TenantAwareTranslatableAdmin):
    """Admin for menu categories with tenant filtering and translation support."""

    tenant_field = "restaurant"

    list_display = ["name", "restaurant", "display_order", "is_active", "items_count"]
    list_filter = ["is_active"]
    search_fields = ["translations__name", "restaurant__name"]
    ordering = ["restaurant", "display_order"]
    actions = ["export_as_csv", "export_as_json", make_active, make_inactive]

    def items_count(self, obj):
        return obj.items_count

    items_count.short_description = "Items"


@admin.register(MenuItem)
class MenuItemAdmin(TenantAwareTranslatableAdmin):
    """Admin for menu items with tenant filtering and translation support."""

    tenant_field = "restaurant"

    list_display = [
        "name",
        "restaurant",
        "category",
        "price",
        "is_available",
        "is_featured",
        "preparation_station",
    ]
    list_filter = [
        "is_available",
        "is_featured",
        "preparation_station",
        "is_vegetarian",
        "is_vegan",
        "is_gluten_free",
        "category",
    ]
    search_fields = ["translations__name", "translations__description", "restaurant__name"]
    list_editable = ["is_available", "is_featured"]
    ordering = ["restaurant", "category", "display_order"]
    inlines = [MenuItemModifierGroupInline]
    actions = ["export_as_csv", "export_as_json", make_active, make_inactive]

    fieldsets = (
        (None, {"fields": ("restaurant", "category", "price", "image")}),
        ("Availability", {"fields": ("is_available", "is_featured", "display_order")}),
        ("Preparation", {"fields": ("preparation_time_minutes", "preparation_station")}),
        (
            "Dietary Information",
            {
                "fields": (
                    "calories",
                    "allergens",
                    "is_vegetarian",
                    "is_vegan",
                    "is_gluten_free",
                    "is_spicy",
                    "spicy_level",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Inventory",
            {
                "fields": ("track_inventory", "stock_quantity"),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(ModifierGroup)
class ModifierGroupAdmin(TenantAwareTranslatableAdmin):
    """Admin for modifier groups with tenant filtering and translation support."""

    tenant_field = "restaurant"

    list_display = [
        "name",
        "restaurant",
        "selection_type",
        "is_required",
        "min_selections",
        "max_selections",
        "is_active",
    ]
    list_filter = ["selection_type", "is_required", "is_active"]
    search_fields = ["translations__name", "restaurant__name"]
    ordering = ["restaurant", "display_order"]
    inlines = [ModifierInline]
    actions = ["export_as_csv", "export_as_json", make_active, make_inactive]


@admin.register(Modifier)
class ModifierAdmin(TenantAwareTranslatableAdmin):
    """Admin for modifiers with tenant filtering and translation support."""

    tenant_field = "group__restaurant"

    list_display = ["name", "group", "price_adjustment", "is_available", "is_default"]
    list_filter = ["is_available", "is_default"]
    search_fields = ["translations__name", "group__translations__name"]
    ordering = ["group", "display_order"]
    actions = ["export_as_csv", "export_as_json"]


@admin.register(MenuItemModifierGroup)
class MenuItemModifierGroupAdmin(TenantAwareModelAdmin):
    """Admin for menu item modifier groups with tenant filtering."""

    tenant_field = "menu_item__restaurant"

    list_display = ["menu_item", "modifier_group", "display_order"]
    list_filter = []
    search_fields = ["menu_item__translations__name", "modifier_group__translations__name"]
    ordering = ["menu_item", "display_order"]
    autocomplete_fields = ["menu_item", "modifier_group"]
