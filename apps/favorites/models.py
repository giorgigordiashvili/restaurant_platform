"""
Favorites models for customers to save restaurants and menu items.
"""

from django.db import models

from apps.core.models import TimeStampedModel


class FavoriteRestaurant(TimeStampedModel):
    """
    Customer's favorite restaurants.
    """

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="favorite_restaurants",
    )
    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="favorited_by",
    )

    class Meta:
        db_table = "favorite_restaurants"
        verbose_name = "Favorite Restaurant"
        verbose_name_plural = "Favorite Restaurants"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "restaurant"],
                name="unique_user_favorite_restaurant",
            )
        ]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["restaurant"]),
        ]

    def __str__(self):
        return f"{self.user.email} - {self.restaurant.name}"


class FavoriteMenuItem(TimeStampedModel):
    """
    Customer's favorite menu items at a specific restaurant.
    """

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="favorite_menu_items",
    )
    menu_item = models.ForeignKey(
        "menu.MenuItem",
        on_delete=models.CASCADE,
        related_name="favorited_by",
    )
    # Denormalized for easier querying
    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="favorite_menu_items",
    )

    class Meta:
        db_table = "favorite_menu_items"
        verbose_name = "Favorite Menu Item"
        verbose_name_plural = "Favorite Menu Items"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "menu_item"],
                name="unique_user_favorite_menu_item",
            )
        ]
        indexes = [
            models.Index(fields=["user", "restaurant"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["menu_item"]),
        ]

    def __str__(self):
        return f"{self.user.email} - {self.menu_item}"

    def save(self, *args, **kwargs):
        """Auto-populate restaurant from menu item."""
        if self.menu_item and not self.restaurant_id:
            self.restaurant = self.menu_item.restaurant
        super().save(*args, **kwargs)
