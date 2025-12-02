"""
Tests for favorites views.
"""

import uuid

from rest_framework import status

import pytest

from apps.favorites.models import FavoriteMenuItem, FavoriteRestaurant


@pytest.mark.django_db
class TestFavoriteRestaurantListView:
    """Tests for favorite restaurant list endpoint."""

    url = "/api/v1/favorites/restaurants/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list favorites."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_list(self, authenticated_client, user, restaurant):
        """Test that authenticated users can list their favorite restaurants."""
        FavoriteRestaurant.objects.create(user=user, restaurant=restaurant)
        response = authenticated_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1
        assert str(response.data["results"][0]["restaurant"]) == str(restaurant.id)

    def test_only_returns_own_favorites(self, authenticated_client, user, another_user, restaurant):
        """Test that users only see their own favorites."""
        FavoriteRestaurant.objects.create(user=another_user, restaurant=restaurant)
        response = authenticated_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0


@pytest.mark.django_db
class TestFavoriteRestaurantCreateView:
    """Tests for favorite restaurant create endpoint."""

    url = "/api/v1/favorites/restaurants/add/"

    def test_unauthenticated_cannot_add(self, api_client, restaurant):
        """Test that unauthenticated users cannot add favorites."""
        response = api_client.post(self.url, {"restaurant": str(restaurant.id)})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_add(self, authenticated_client, restaurant):
        """Test that authenticated users can add restaurants to favorites."""
        response = authenticated_client.post(self.url, {"restaurant": str(restaurant.id)}, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert FavoriteRestaurant.objects.count() == 1

    def test_cannot_add_duplicate(self, authenticated_client, user, restaurant):
        """Test that users cannot add the same restaurant twice."""
        FavoriteRestaurant.objects.create(user=user, restaurant=restaurant)
        response = authenticated_client.post(self.url, {"restaurant": str(restaurant.id)}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestFavoriteRestaurantDeleteView:
    """Tests for favorite restaurant delete endpoint."""

    def test_unauthenticated_cannot_delete(self, api_client, restaurant):
        """Test that unauthenticated users cannot delete favorites."""
        url = f"/api/v1/favorites/restaurants/{restaurant.id}/remove/"
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_delete(self, authenticated_client, user, restaurant):
        """Test that authenticated users can delete their favorites."""
        FavoriteRestaurant.objects.create(user=user, restaurant=restaurant)
        url = f"/api/v1/favorites/restaurants/{restaurant.id}/remove/"
        response = authenticated_client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert FavoriteRestaurant.objects.count() == 0

    def test_delete_not_found(self, authenticated_client, restaurant):
        """Test deleting non-existent favorite."""
        url = f"/api/v1/favorites/restaurants/{restaurant.id}/remove/"
        response = authenticated_client.delete(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestFavoriteRestaurantToggleView:
    """Tests for favorite restaurant toggle endpoint."""

    def test_toggle_adds_favorite(self, authenticated_client, restaurant):
        """Test toggle adds restaurant to favorites."""
        url = f"/api/v1/favorites/restaurants/{restaurant.id}/toggle/"
        response = authenticated_client.post(url)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["is_favorited"] is True
        assert FavoriteRestaurant.objects.count() == 1

    def test_toggle_removes_favorite(self, authenticated_client, user, restaurant):
        """Test toggle removes restaurant from favorites."""
        FavoriteRestaurant.objects.create(user=user, restaurant=restaurant)
        url = f"/api/v1/favorites/restaurants/{restaurant.id}/toggle/"
        response = authenticated_client.post(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_favorited"] is False
        assert FavoriteRestaurant.objects.count() == 0

    def test_toggle_not_found(self, authenticated_client):
        """Test toggle with non-existent restaurant."""
        url = f"/api/v1/favorites/restaurants/{uuid.uuid4()}/toggle/"
        response = authenticated_client.post(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestFavoriteRestaurantStatusView:
    """Tests for favorite restaurant status endpoint."""

    def test_status_when_favorited(self, authenticated_client, user, restaurant):
        """Test status returns true when restaurant is favorited."""
        FavoriteRestaurant.objects.create(user=user, restaurant=restaurant)
        url = f"/api/v1/favorites/restaurants/{restaurant.id}/status/"
        response = authenticated_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_favorited"] is True

    def test_status_when_not_favorited(self, authenticated_client, restaurant):
        """Test status returns false when restaurant is not favorited."""
        url = f"/api/v1/favorites/restaurants/{restaurant.id}/status/"
        response = authenticated_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_favorited"] is False


@pytest.mark.django_db
class TestFavoriteMenuItemListView:
    """Tests for favorite menu item list endpoint."""

    url = "/api/v1/favorites/menu-items/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list favorites."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_list(self, authenticated_client, user, menu_item, restaurant):
        """Test that authenticated users can list their favorite menu items."""
        FavoriteMenuItem.objects.create(user=user, menu_item=menu_item, restaurant=restaurant)
        response = authenticated_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1

    def test_filter_by_restaurant(self, authenticated_client, user, menu_item, restaurant, another_restaurant):
        """Test filtering favorites by restaurant."""
        FavoriteMenuItem.objects.create(user=user, menu_item=menu_item, restaurant=restaurant)
        response = authenticated_client.get(self.url, {"restaurant": str(restaurant.id)})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1


@pytest.mark.django_db
class TestFavoriteMenuItemCreateView:
    """Tests for favorite menu item create endpoint."""

    url = "/api/v1/favorites/menu-items/add/"

    def test_unauthenticated_cannot_add(self, api_client, menu_item):
        """Test that unauthenticated users cannot add favorites."""
        response = api_client.post(self.url, {"menu_item": str(menu_item.id)})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_add(self, authenticated_client, menu_item):
        """Test that authenticated users can add menu items to favorites."""
        response = authenticated_client.post(self.url, {"menu_item": str(menu_item.id)}, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert FavoriteMenuItem.objects.count() == 1

    def test_cannot_add_duplicate(self, authenticated_client, user, menu_item, restaurant):
        """Test that users cannot add the same menu item twice."""
        FavoriteMenuItem.objects.create(user=user, menu_item=menu_item, restaurant=restaurant)
        response = authenticated_client.post(self.url, {"menu_item": str(menu_item.id)}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestFavoriteMenuItemToggleView:
    """Tests for favorite menu item toggle endpoint."""

    def test_toggle_adds_favorite(self, authenticated_client, menu_item):
        """Test toggle adds menu item to favorites."""
        url = f"/api/v1/favorites/menu-items/{menu_item.id}/toggle/"
        response = authenticated_client.post(url)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["is_favorited"] is True
        assert FavoriteMenuItem.objects.count() == 1

    def test_toggle_removes_favorite(self, authenticated_client, user, menu_item, restaurant):
        """Test toggle removes menu item from favorites."""
        FavoriteMenuItem.objects.create(user=user, menu_item=menu_item, restaurant=restaurant)
        url = f"/api/v1/favorites/menu-items/{menu_item.id}/toggle/"
        response = authenticated_client.post(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["is_favorited"] is False
        assert FavoriteMenuItem.objects.count() == 0


@pytest.mark.django_db
class TestFavoriteCountsView:
    """Tests for favorite counts endpoint."""

    url = "/api/v1/favorites/counts/"

    def test_unauthenticated_cannot_access(self, api_client):
        """Test that unauthenticated users cannot access counts."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_returns_counts(self, authenticated_client, user, restaurant, menu_item):
        """Test that counts are returned correctly."""
        FavoriteRestaurant.objects.create(user=user, restaurant=restaurant)
        FavoriteMenuItem.objects.create(user=user, menu_item=menu_item, restaurant=restaurant)
        response = authenticated_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["restaurants"] == 1
        assert response.data["menu_items"] == 1


@pytest.mark.django_db
class TestClearAllFavoritesView:
    """Tests for clear all favorites endpoint."""

    url = "/api/v1/favorites/clear/"

    def test_unauthenticated_cannot_clear(self, api_client):
        """Test that unauthenticated users cannot clear favorites."""
        response = api_client.delete(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_clears_all_favorites(self, authenticated_client, user, restaurant, menu_item):
        """Test that all favorites are cleared."""
        FavoriteRestaurant.objects.create(user=user, restaurant=restaurant)
        FavoriteMenuItem.objects.create(user=user, menu_item=menu_item, restaurant=restaurant)
        response = authenticated_client.delete(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert FavoriteRestaurant.objects.count() == 0
        assert FavoriteMenuItem.objects.count() == 0
