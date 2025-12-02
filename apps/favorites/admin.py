"""
Admin configuration for favorites app with multi-tenant support.
"""

from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import FavoriteMenuItem, FavoriteRestaurant


@admin.register(FavoriteRestaurant)
class FavoriteRestaurantAdmin(TenantAwareModelAdmin):
    """Admin for FavoriteRestaurant model with tenant filtering."""

    tenant_field = "restaurant"

    list_display = ["id", "user_email", "restaurant_name", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["user__email", "restaurant__name"]
    raw_id_fields = ["user", "restaurant"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["-created_at"]

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = "User"
    user_email.admin_order_field = "user__email"

    def restaurant_name(self, obj):
        return obj.restaurant.name

    restaurant_name.short_description = "Restaurant"
    restaurant_name.admin_order_field = "restaurant__name"


@admin.register(FavoriteMenuItem)
class FavoriteMenuItemAdmin(TenantAwareModelAdmin):
    """Admin for FavoriteMenuItem model with tenant filtering."""

    tenant_field = "restaurant"

    list_display = ["id", "user_email", "menu_item_name", "restaurant_name", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["user__email", "menu_item__translations__name", "restaurant__name"]
    raw_id_fields = ["user", "menu_item", "restaurant"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["-created_at"]

    def user_email(self, obj):
        return obj.user.email

    user_email.short_description = "User"
    user_email.admin_order_field = "user__email"

    def menu_item_name(self, obj):
        return str(obj.menu_item)

    menu_item_name.short_description = "Menu Item"

    def restaurant_name(self, obj):
        return obj.restaurant.name

    restaurant_name.short_description = "Restaurant"
    restaurant_name.admin_order_field = "restaurant__name"
