"""
Tests for tenant (restaurant) views.
"""

from rest_framework import status

import pytest


@pytest.mark.django_db
class TestRestaurantListViewWithTranslations:
    """Tests for restaurant list endpoint with translated category and amenities."""

    url = "/api/v1/restaurants/"

    def test_list_includes_category_translations(
        self, api_client, restaurant_with_category_and_amenities
    ):
        """Test that restaurant list includes category with translations."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK

        restaurant_data = response.data["results"][0]
        assert "category" in restaurant_data
        assert restaurant_data["category"] is not None
        assert "translations" in restaurant_data["category"]

    def test_list_includes_amenities_translations(
        self, api_client, restaurant_with_category_and_amenities
    ):
        """Test that restaurant list includes amenities with translations."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK

        restaurant_data = response.data["results"][0]
        assert "amenities" in restaurant_data
        assert len(restaurant_data["amenities"]) >= 1
        assert "translations" in restaurant_data["amenities"][0]

    def test_list_with_language_parameter(
        self, api_client, restaurant_with_category_and_amenities
    ):
        """Test restaurant list with ?lang= parameter."""
        # Request with English
        response = api_client.get(self.url, {"lang": "en"})
        assert response.status_code == status.HTTP_200_OK
        assert response.headers.get("Content-Language") == "en"

        # Request with Georgian
        response = api_client.get(self.url, {"lang": "ka"})
        assert response.status_code == status.HTTP_200_OK
        assert response.headers.get("Content-Language") == "ka"

    def test_list_with_accept_language_header(
        self, api_client, restaurant_with_category_and_amenities
    ):
        """Test restaurant list with Accept-Language header."""
        response = api_client.get(
            self.url, HTTP_ACCEPT_LANGUAGE="en,ka;q=0.9"
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.headers.get("Content-Language") == "en"


@pytest.mark.django_db
class TestRestaurantDetailViewWithTranslations:
    """Tests for restaurant detail endpoint with translated category and amenities."""

    def test_detail_includes_category_translations(
        self, api_client, restaurant_with_category_and_amenities
    ):
        """Test that restaurant detail includes category with translations."""
        url = f"/api/v1/restaurants/{restaurant_with_category_and_amenities.slug}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK

        assert "category" in response.data
        assert response.data["category"] is not None
        assert "translations" in response.data["category"]

    def test_detail_includes_amenities_translations(
        self, api_client, restaurant_with_category_and_amenities
    ):
        """Test that restaurant detail includes amenities with translations."""
        url = f"/api/v1/restaurants/{restaurant_with_category_and_amenities.slug}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK

        assert "amenities" in response.data
        assert len(response.data["amenities"]) >= 1
        assert "translations" in response.data["amenities"][0]

    def test_detail_with_language_parameter(
        self, api_client, restaurant_with_category_and_amenities
    ):
        """Test restaurant detail with ?lang= parameter."""
        url = f"/api/v1/restaurants/{restaurant_with_category_and_amenities.slug}/"

        response = api_client.get(url, {"lang": "ru"})
        assert response.status_code == status.HTTP_200_OK
        assert response.headers.get("Content-Language") == "ru"


@pytest.mark.django_db
class TestRestaurantListView:
    """Tests for restaurant list endpoint."""

    url = "/api/v1/restaurants/"

    def test_list_restaurants(self, api_client, restaurant):
        """Test listing active restaurants."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) >= 1

    def test_list_excludes_inactive(self, api_client, restaurant):
        """Test that inactive restaurants are excluded."""
        restaurant.is_active = False
        restaurant.save()

        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        slugs = [r["slug"] for r in response.data["results"]]
        assert restaurant.slug not in slugs

    def test_search_restaurants(self, api_client, restaurant):
        """Test searching restaurants by name."""
        response = api_client.get(self.url, {"search": restaurant.name})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) >= 1

    def test_filter_by_city(self, api_client, restaurant):
        """Test filtering restaurants by city."""
        restaurant.city = "Tbilisi"
        restaurant.save()

        response = api_client.get(self.url, {"city": "Tbilisi"})
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestRestaurantDetailView:
    """Tests for restaurant detail endpoint."""

    def test_get_restaurant_detail(self, api_client, restaurant):
        """Test getting restaurant details."""
        url = f"/api/v1/restaurants/{restaurant.slug}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == restaurant.name
        assert response.data["slug"] == restaurant.slug

    def test_restaurant_not_found(self, api_client):
        """Test 404 for non-existent restaurant."""
        url = "/api/v1/restaurants/non-existent/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_inactive_restaurant_not_accessible(self, api_client, restaurant):
        """Test that inactive restaurant returns 404."""
        restaurant.is_active = False
        restaurant.save()

        url = f"/api/v1/restaurants/{restaurant.slug}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestRestaurantHoursView:
    """Tests for restaurant hours endpoint."""

    def test_get_restaurant_hours(self, api_client, restaurant_with_hours):
        """Test getting restaurant operating hours."""
        url = f"/api/v1/restaurants/{restaurant_with_hours.slug}/hours/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert len(response.data["data"]["hours"]) == 7


@pytest.mark.django_db
class TestRestaurantCreateView:
    """Tests for restaurant creation endpoint."""

    url = "/api/v1/restaurants/create/"

    def test_create_restaurant_authenticated(self, authenticated_client):
        """Test creating a restaurant when authenticated."""
        data = {
            "name": "New Restaurant",
            "slug": "new-restaurant",
            "description": "A new restaurant",
        }
        response = authenticated_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["success"] is True
        assert response.data["data"]["name"] == "New Restaurant"

    def test_create_restaurant_unauthenticated(self, api_client):
        """Test creating a restaurant when not authenticated."""
        data = {
            "name": "New Restaurant",
            "slug": "new-restaurant",
        }
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_create_restaurant_duplicate_slug(self, authenticated_client, restaurant):
        """Test creating a restaurant with duplicate slug."""
        data = {
            "name": "Another Restaurant",
            "slug": restaurant.slug,  # Duplicate
        }
        response = authenticated_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
