"""
Menu admin configuration with translation support.
"""

from django.contrib import admin
from parler.admin import TranslatableAdmin, TranslatableTabularInline

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
class MenuCategoryAdmin(TranslatableAdmin):
    list_display = ["name", "restaurant", "display_order", "is_active", "items_count"]
    list_filter = ["is_active", "restaurant"]
    search_fields = ["translations__name", "restaurant__name"]
    ordering = ["restaurant", "display_order"]

    def items_count(self, obj):
        return obj.items_count

    items_count.short_description = "Items"


@admin.register(MenuItem)
class MenuItemAdmin(TranslatableAdmin):
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
        "restaurant",
        "category",
    ]
    search_fields = ["translations__name", "translations__description", "restaurant__name"]
    list_editable = ["is_available", "is_featured"]
    ordering = ["restaurant", "category", "display_order"]
    inlines = [MenuItemModifierGroupInline]

    fieldsets = (
        (None, {
            "fields": ("restaurant", "category", "price", "image")
        }),
        ("Availability", {
            "fields": ("is_available", "is_featured", "display_order")
        }),
        ("Preparation", {
            "fields": ("preparation_time_minutes", "preparation_station")
        }),
        ("Dietary Information", {
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
        }),
        ("Inventory", {
            "fields": ("track_inventory", "stock_quantity"),
            "classes": ("collapse",),
        }),
    )


@admin.register(ModifierGroup)
class ModifierGroupAdmin(TranslatableAdmin):
    list_display = [
        "name",
        "restaurant",
        "selection_type",
        "is_required",
        "min_selections",
        "max_selections",
        "is_active",
    ]
    list_filter = ["selection_type", "is_required", "is_active", "restaurant"]
    search_fields = ["translations__name", "restaurant__name"]
    ordering = ["restaurant", "display_order"]
    inlines = [ModifierInline]


@admin.register(Modifier)
class ModifierAdmin(TranslatableAdmin):
    list_display = ["name", "group", "price_adjustment", "is_available", "is_default"]
    list_filter = ["is_available", "is_default", "group__restaurant"]
    search_fields = ["translations__name", "group__translations__name"]
    ordering = ["group", "display_order"]


@admin.register(MenuItemModifierGroup)
class MenuItemModifierGroupAdmin(admin.ModelAdmin):
    list_display = ["menu_item", "modifier_group", "display_order"]
    list_filter = ["menu_item__restaurant"]
    search_fields = ["menu_item__translations__name", "modifier_group__translations__name"]
    ordering = ["menu_item", "display_order"]
    autocomplete_fields = ["menu_item", "modifier_group"]
