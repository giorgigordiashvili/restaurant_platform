"""
Tests for tenant (restaurant) models.
"""

from datetime import time
from decimal import Decimal

import pytest

from apps.tenants.models import Amenity, Restaurant, RestaurantCategory, RestaurantHours


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


@pytest.mark.django_db
class TestRestaurantCategoryModel:
    """Tests for RestaurantCategory model with translations."""

    def test_create_category(self, db):
        """Test creating a restaurant category with translation."""
        category = RestaurantCategory.objects.create(slug="italian")
        category.set_current_language("en")
        category.name = "Italian"
        category.description = "Italian cuisine"
        category.save()

        assert category.name == "Italian"
        assert category.slug == "italian"
        assert category.is_active is True

    def test_category_str(self, restaurant_category):
        """Test category string representation uses translated name."""
        # The fixture creates with Georgian name "რესტორანი"
        assert "რესტორანი" in str(restaurant_category) or "Category" in str(restaurant_category)

    def test_category_with_multiple_translations(self, restaurant_category_with_translations):
        """Test category with Georgian, English, and Russian translations."""
        category = restaurant_category_with_translations

        # Test Georgian
        category.set_current_language("ka")
        assert category.name == "ქართული სამზარეულო"
        assert category.description == "ტრადიციული ქართული კერძები"

        # Test English
        category.set_current_language("en")
        assert category.name == "Georgian Cuisine"
        assert category.description == "Traditional Georgian dishes"

        # Test Russian
        category.set_current_language("ru")
        assert category.name == "Грузинская кухня"
        assert category.description == "Традиционные грузинские блюда"

    def test_safe_translation_getter(self, restaurant_category_with_translations):
        """Test safe_translation_getter fallback behavior."""
        category = restaurant_category_with_translations

        # Access name via safe_translation_getter
        name = category.safe_translation_getter("name", default="Fallback")
        assert name in ["ქართული სამზარეულო", "Georgian Cuisine", "Грузинская кухня"]

    def test_category_ordering(self, create_restaurant_category):
        """Test categories are ordered by display_order."""
        cat3 = create_restaurant_category(name="Third", slug="third", display_order=3)
        cat1 = create_restaurant_category(name="First", slug="first", display_order=1)
        cat2 = create_restaurant_category(name="Second", slug="second", display_order=2)

        categories = list(RestaurantCategory.objects.all())
        assert categories[0].display_order == 1
        assert categories[1].display_order == 2
        assert categories[2].display_order == 3

    def test_restaurants_count_property(self, restaurant_category, restaurant):
        """Test restaurants_count property."""
        restaurant.category = restaurant_category
        restaurant.save()

        assert restaurant_category.restaurants_count == 1

    def test_unique_slug(self, db):
        """Test slug uniqueness."""
        RestaurantCategory.objects.create(slug="unique-slug")

        with pytest.raises(Exception):
            RestaurantCategory.objects.create(slug="unique-slug")

    def test_translation_fallback(self, db):
        """Test that translation falls back gracefully."""
        category = RestaurantCategory.objects.create(slug="test-fallback")
        category.set_current_language("en")
        category.name = "English Name"
        category.save()

        # Try to get name in a language that doesn't exist
        category.set_current_language("ka")
        # safe_translation_getter should return the fallback
        name = category.safe_translation_getter("name", default="Fallback")
        assert name in ["English Name", "Fallback"]


@pytest.mark.django_db
class TestAmenityModel:
    """Tests for Amenity model with translations."""

    def test_create_amenity(self, db):
        """Test creating an amenity with translation."""
        amenity = Amenity.objects.create(slug="wifi", icon="wifi")
        amenity.set_current_language("en")
        amenity.name = "WiFi"
        amenity.description = "Free wireless internet"
        amenity.save()

        assert amenity.name == "WiFi"
        assert amenity.slug == "wifi"
        assert amenity.icon == "wifi"
        assert amenity.is_active is True

    def test_amenity_str(self, amenity):
        """Test amenity string representation uses translated name."""
        # The fixture creates with Georgian name "ტერასა"
        assert "ტერასა" in str(amenity) or "Amenity" in str(amenity)

    def test_amenity_with_multiple_translations(self, amenity_with_translations):
        """Test amenity with Georgian, English, and Russian translations."""
        amenity = amenity_with_translations

        # Test Georgian
        amenity.set_current_language("ka")
        assert amenity.name == "ცოცხალი მუსიკა"
        assert amenity.description == "ცოცხალი მუსიკალური გამოსვლები"

        # Test English
        amenity.set_current_language("en")
        assert amenity.name == "Live Music"
        assert amenity.description == "Live musical performances"

        # Test Russian
        amenity.set_current_language("ru")
        assert amenity.name == "Живая музыка"
        assert amenity.description == "Живые музыкальные выступления"

    def test_amenity_ordering(self, create_amenity):
        """Test amenities are ordered by display_order."""
        am3 = create_amenity(name="Third", slug="third", display_order=3)
        am1 = create_amenity(name="First", slug="first", display_order=1)
        am2 = create_amenity(name="Second", slug="second", display_order=2)

        amenities = list(Amenity.objects.all())
        assert amenities[0].display_order == 1
        assert amenities[1].display_order == 2
        assert amenities[2].display_order == 3

    def test_unique_slug(self, db):
        """Test slug uniqueness."""
        Amenity.objects.create(slug="unique-amenity")

        with pytest.raises(Exception):
            Amenity.objects.create(slug="unique-amenity")

    def test_restaurant_amenities_relationship(self, restaurant, amenity, create_amenity):
        """Test many-to-many relationship between restaurants and amenities."""
        amenity2 = create_amenity(name="WiFi", slug="wifi", icon="wifi")

        restaurant.amenities.add(amenity)
        restaurant.amenities.add(amenity2)

        assert restaurant.amenities.count() == 2
        assert amenity in restaurant.amenities.all()
        assert amenity2 in restaurant.amenities.all()

    def test_safe_translation_getter(self, amenity_with_translations):
        """Test safe_translation_getter fallback behavior."""
        amenity = amenity_with_translations

        name = amenity.safe_translation_getter("name", default="Fallback")
        assert name in ["ცოცხალი მუსიკა", "Live Music", "Живая музыка"]


@pytest.mark.django_db
class TestRestaurantWithCategoryAndAmenities:
    """Tests for Restaurant model with category and amenities."""

    def test_restaurant_with_category(self, restaurant, restaurant_category):
        """Test assigning a category to a restaurant."""
        restaurant.category = restaurant_category
        restaurant.save()

        restaurant.refresh_from_db()
        assert restaurant.category == restaurant_category
        assert restaurant in restaurant_category.restaurants.all()

    def test_restaurant_with_multiple_amenities(self, restaurant, create_amenity):
        """Test assigning multiple amenities to a restaurant."""
        terrace = create_amenity(name="Terrace", slug="terrace", icon="deck")
        wifi = create_amenity(name="WiFi", slug="wifi", icon="wifi")
        music = create_amenity(name="Live Music", slug="live-music", icon="music_note")

        restaurant.amenities.add(terrace, wifi, music)

        assert restaurant.amenities.count() == 3

    def test_category_null_on_delete(self, restaurant, restaurant_category):
        """Test that deleting category sets restaurant.category to NULL."""
        restaurant.category = restaurant_category
        restaurant.save()

        restaurant_category.delete()
        restaurant.refresh_from_db()

        assert restaurant.category is None

    def test_amenity_relationship_preserved(self, restaurant, amenity):
        """Test that amenity many-to-many relationship is preserved correctly."""
        restaurant.amenities.add(amenity)

        # Verify from both sides
        assert restaurant.amenities.count() == 1
        assert restaurant in amenity.restaurants.all()
