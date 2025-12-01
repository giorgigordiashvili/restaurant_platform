"""
Tests for tenant (restaurant) models.
"""

import pytest
from datetime import time
from decimal import Decimal

from apps.tenants.models import Restaurant, RestaurantHours


@pytest.mark.django_db
class TestRestaurantModel:
    """Tests for Restaurant model."""

    def test_create_restaurant(self, user):
        """Test creating a restaurant."""
        restaurant = Restaurant.objects.create(
            owner=user,
            name="Test Restaurant",
            slug="test-restaurant",
        )
        assert restaurant.name == "Test Restaurant"
        assert restaurant.slug == "test-restaurant"
        assert restaurant.owner == user
        assert restaurant.is_active is True

    def test_restaurant_str(self, restaurant):
        """Test restaurant string representation."""
        assert str(restaurant) == restaurant.name

    def test_auto_slug_generation(self, user):
        """Test auto-generation of slug from name."""
        restaurant = Restaurant.objects.create(
            owner=user,
            name="My Great Restaurant",
        )
        assert restaurant.slug == "my-great-restaurant"

    def test_unique_slug_generation(self, user, restaurant):
        """Test unique slug generation when name conflicts."""
        new_restaurant = Restaurant.objects.create(
            owner=user,
            name="Test Restaurant",  # Same name as existing
        )
        assert new_restaurant.slug != restaurant.slug
        assert new_restaurant.slug.startswith("test-restaurant")

    def test_full_address(self, restaurant):
        """Test full address property."""
        restaurant.address = "123 Main St"
        restaurant.city = "Tbilisi"
        restaurant.postal_code = "0100"
        restaurant.country = "Georgia"
        restaurant.save()

        assert "123 Main St" in restaurant.full_address
        assert "Tbilisi" in restaurant.full_address
        assert "Georgia" in restaurant.full_address

    def test_default_values(self, restaurant):
        """Test default field values."""
        assert restaurant.default_currency == "GEL"
        assert restaurant.timezone == "Asia/Tbilisi"
        assert restaurant.default_language == "ka"
        assert restaurant.accepts_remote_orders is True
        assert restaurant.accepts_reservations is True
        assert restaurant.tax_rate == Decimal("0")
        assert restaurant.average_rating == Decimal("0")


@pytest.mark.django_db
class TestRestaurantHoursModel:
    """Tests for RestaurantHours model."""

    def test_create_hours(self, restaurant):
        """Test creating operating hours."""
        hours = RestaurantHours.objects.create(
            restaurant=restaurant,
            day_of_week=0,  # Monday
            open_time=time(9, 0),
            close_time=time(22, 0),
        )
        assert hours.day_of_week == 0
        assert hours.open_time == time(9, 0)
        assert hours.close_time == time(22, 0)
        assert hours.is_closed is False

    def test_hours_str(self, restaurant):
        """Test hours string representation."""
        hours = RestaurantHours.objects.create(
            restaurant=restaurant,
            day_of_week=0,
            open_time=time(9, 0),
            close_time=time(22, 0),
        )
        assert restaurant.name in str(hours)
        assert "Monday" in str(hours)

    def test_closed_day_str(self, restaurant):
        """Test string representation for closed day."""
        hours = RestaurantHours.objects.create(
            restaurant=restaurant,
            day_of_week=6,  # Sunday
            open_time=time(9, 0),
            close_time=time(22, 0),
            is_closed=True,
        )
        assert "Closed" in str(hours)

    def test_create_default_hours(self, restaurant):
        """Test creating default operating hours."""
        hours = RestaurantHours.create_default_hours(restaurant)
        assert len(hours) == 7
        for i, hour in enumerate(hours):
            assert hour.day_of_week == i
            assert hour.open_time == time(9, 0)
            assert hour.close_time == time(22, 0)

    def test_unique_constraint(self, restaurant):
        """Test unique constraint on restaurant + day_of_week."""
        RestaurantHours.objects.create(
            restaurant=restaurant,
            day_of_week=0,
            open_time=time(9, 0),
            close_time=time(22, 0),
        )
        with pytest.raises(Exception):
            RestaurantHours.objects.create(
                restaurant=restaurant,
                day_of_week=0,  # Same day
                open_time=time(10, 0),
                close_time=time(23, 0),
            )
