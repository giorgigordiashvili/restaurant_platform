"""
Tests for favorites models.
"""

from django.db import IntegrityError

import pytest

from apps.favorites.models import FavoriteMenuItem, FavoriteRestaurant


@pytest.mark.django_db
class TestFavoriteRestaurantModel:
    """Tests for FavoriteRestaurant model."""

    def test_create_favorite_restaurant(self, user, restaurant):
        """Test creating a favorite restaurant."""
        favorite = FavoriteRestaurant.objects.create(
            user=user,
            restaurant=restaurant,
        )
        assert favorite.user == user
        assert favorite.restaurant == restaurant
        assert favorite.created_at is not None

    def test_favorite_restaurant_str(self, user, restaurant):
        """Test favorite restaurant string representation."""
        favorite = FavoriteRestaurant.objects.create(
            user=user,
            restaurant=restaurant,
        )
        assert user.email in str(favorite)
        assert restaurant.name in str(favorite)

    def test_unique_constraint(self, user, restaurant):
        """Test that user can only favorite a restaurant once."""
        FavoriteRestaurant.objects.create(user=user, restaurant=restaurant)

        with pytest.raises(IntegrityError):
            FavoriteRestaurant.objects.create(user=user, restaurant=restaurant)

    def test_multiple_users_can_favorite_same_restaurant(self, user, another_user, restaurant):
        """Test that multiple users can favorite the same restaurant."""
        fav1 = FavoriteRestaurant.objects.create(user=user, restaurant=restaurant)
        fav2 = FavoriteRestaurant.objects.create(user=another_user, restaurant=restaurant)

        assert fav1.pk != fav2.pk
        assert FavoriteRestaurant.objects.filter(restaurant=restaurant).count() == 2

    def test_user_can_favorite_multiple_restaurants(self, user, restaurant, another_restaurant):
        """Test that a user can favorite multiple restaurants."""
        fav1 = FavoriteRestaurant.objects.create(user=user, restaurant=restaurant)
        fav2 = FavoriteRestaurant.objects.create(user=user, restaurant=another_restaurant)

        assert fav1.pk != fav2.pk
        assert FavoriteRestaurant.objects.filter(user=user).count() == 2


@pytest.mark.django_db
class TestFavoriteMenuItemModel:
    """Tests for FavoriteMenuItem model."""

    def test_create_favorite_menu_item(self, user, menu_item, restaurant):
        """Test creating a favorite menu item."""
        favorite = FavoriteMenuItem.objects.create(
            user=user,
            menu_item=menu_item,
            restaurant=restaurant,
        )
        assert favorite.user == user
        assert favorite.menu_item == menu_item
        assert favorite.restaurant == restaurant
        assert favorite.created_at is not None

    def test_favorite_menu_item_str(self, user, menu_item, restaurant):
        """Test favorite menu item string representation."""
        favorite = FavoriteMenuItem.objects.create(
            user=user,
            menu_item=menu_item,
            restaurant=restaurant,
        )
        assert user.email in str(favorite)

    def test_auto_populate_restaurant(self, user, menu_item):
        """Test that restaurant is auto-populated from menu item."""
        favorite = FavoriteMenuItem(user=user, menu_item=menu_item)
        favorite.save()

        assert favorite.restaurant == menu_item.restaurant

    def test_unique_constraint(self, user, menu_item, restaurant):
        """Test that user can only favorite a menu item once."""
        FavoriteMenuItem.objects.create(user=user, menu_item=menu_item, restaurant=restaurant)

        with pytest.raises(IntegrityError):
            FavoriteMenuItem.objects.create(user=user, menu_item=menu_item, restaurant=restaurant)

    def test_multiple_users_can_favorite_same_item(self, user, another_user, menu_item, restaurant):
        """Test that multiple users can favorite the same menu item."""
        fav1 = FavoriteMenuItem.objects.create(user=user, menu_item=menu_item, restaurant=restaurant)
        fav2 = FavoriteMenuItem.objects.create(user=another_user, menu_item=menu_item, restaurant=restaurant)

        assert fav1.pk != fav2.pk
        assert FavoriteMenuItem.objects.filter(menu_item=menu_item).count() == 2

    def test_user_can_favorite_multiple_items(self, user, menu_item, create_menu_item, restaurant):
        """Test that a user can favorite multiple menu items."""
        menu_item2 = create_menu_item(restaurant=restaurant, name="Another Item")

        fav1 = FavoriteMenuItem.objects.create(user=user, menu_item=menu_item, restaurant=restaurant)
        fav2 = FavoriteMenuItem.objects.create(user=user, menu_item=menu_item2, restaurant=restaurant)

        assert fav1.pk != fav2.pk
        assert FavoriteMenuItem.objects.filter(user=user).count() == 2
