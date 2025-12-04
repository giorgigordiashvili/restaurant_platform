"""
Tests for tenant serializers with translation support.
"""

import pytest
from django.utils import translation

from apps.tenants.serializers import (
    AmenitySerializer,
    RestaurantCategorySerializer,
    RestaurantDetailSerializer,
    RestaurantListSerializer,
)


@pytest.mark.django_db
class TestRestaurantCategorySerializer:
    """Tests for RestaurantCategorySerializer with translations."""

    def test_serializer_output_format(self, restaurant_category_with_translations):
        """Test that serializer returns translations in correct format."""
        serializer = RestaurantCategorySerializer(restaurant_category_with_translations)
        data = serializer.data

        assert "id" in data
        assert "slug" in data
        assert "icon" in data
        assert "translations" in data

    def test_translations_contain_all_languages(self, restaurant_category_with_translations):
        """Test that all language translations are included."""
        serializer = RestaurantCategorySerializer(restaurant_category_with_translations)
        translations = serializer.data["translations"]

        assert "ka" in translations
        assert "en" in translations
        assert "ru" in translations

    def test_translation_fields(self, restaurant_category_with_translations):
        """Test that translation objects contain name and description."""
        serializer = RestaurantCategorySerializer(restaurant_category_with_translations)
        translations = serializer.data["translations"]

        # Check English translation
        assert translations["en"]["name"] == "Georgian Cuisine"
        assert translations["en"]["description"] == "Traditional Georgian dishes"

        # Check Georgian translation
        assert translations["ka"]["name"] == "ქართული სამზარეულო"
        assert translations["ka"]["description"] == "ტრადიციული ქართული კერძები"

        # Check Russian translation
        assert translations["ru"]["name"] == "Грузинская кухня"
        assert translations["ru"]["description"] == "Традиционные грузинские блюда"

    def test_slug_in_output(self, restaurant_category_with_translations):
        """Test that slug is included in serializer output."""
        serializer = RestaurantCategorySerializer(restaurant_category_with_translations)
        data = serializer.data

        assert data["slug"] == "georgian-cuisine"

    def test_icon_in_output(self, restaurant_category_with_translations):
        """Test that icon is included in serializer output."""
        serializer = RestaurantCategorySerializer(restaurant_category_with_translations)
        data = serializer.data

        assert data["icon"] == "restaurant"

    def test_single_language_category(self, restaurant_category):
        """Test serializer with category having only one translation."""
        serializer = RestaurantCategorySerializer(restaurant_category)
        data = serializer.data

        assert "translations" in data
        assert "ka" in data["translations"]
        assert data["translations"]["ka"]["name"] == "რესტორანი"


@pytest.mark.django_db
class TestAmenitySerializer:
    """Tests for AmenitySerializer with translations."""

    def test_serializer_output_format(self, amenity_with_translations):
        """Test that serializer returns translations in correct format."""
        serializer = AmenitySerializer(amenity_with_translations)
        data = serializer.data

        assert "id" in data
        assert "slug" in data
        assert "icon" in data
        assert "translations" in data

    def test_translations_contain_all_languages(self, amenity_with_translations):
        """Test that all language translations are included."""
        serializer = AmenitySerializer(amenity_with_translations)
        translations = serializer.data["translations"]

        assert "ka" in translations
        assert "en" in translations
        assert "ru" in translations

    def test_translation_fields(self, amenity_with_translations):
        """Test that translation objects contain name and description."""
        serializer = AmenitySerializer(amenity_with_translations)
        translations = serializer.data["translations"]

        # Check English translation
        assert translations["en"]["name"] == "Live Music"
        assert translations["en"]["description"] == "Live musical performances"

        # Check Georgian translation
        assert translations["ka"]["name"] == "ცოცხალი მუსიკა"
        assert translations["ka"]["description"] == "ცოცხალი მუსიკალური გამოსვლები"

        # Check Russian translation
        assert translations["ru"]["name"] == "Живая музыка"
        assert translations["ru"]["description"] == "Живые музыкальные выступления"

    def test_slug_in_output(self, amenity_with_translations):
        """Test that slug is included in serializer output."""
        serializer = AmenitySerializer(amenity_with_translations)
        data = serializer.data

        assert data["slug"] == "live-music"

    def test_icon_in_output(self, amenity_with_translations):
        """Test that icon is included in serializer output."""
        serializer = AmenitySerializer(amenity_with_translations)
        data = serializer.data

        assert data["icon"] == "music_note"

    def test_single_language_amenity(self, amenity):
        """Test serializer with amenity having only one translation."""
        serializer = AmenitySerializer(amenity)
        data = serializer.data

        assert "translations" in data
        assert "ka" in data["translations"]
        assert data["translations"]["ka"]["name"] == "ტერასა"


@pytest.mark.django_db
class TestRestaurantSerializersWithCategoryAndAmenities:
    """Tests for restaurant serializers including category and amenities."""

    def test_restaurant_list_includes_category(self, restaurant_with_category_and_amenities):
        """Test that RestaurantListSerializer includes category with translations."""
        serializer = RestaurantListSerializer(restaurant_with_category_and_amenities)
        data = serializer.data

        assert "category" in data
        assert data["category"] is not None
        assert "translations" in data["category"]

    def test_restaurant_list_includes_amenities(self, restaurant_with_category_and_amenities):
        """Test that RestaurantListSerializer includes amenities with translations."""
        serializer = RestaurantListSerializer(restaurant_with_category_and_amenities)
        data = serializer.data

        assert "amenities" in data
        assert len(data["amenities"]) == 1
        assert "translations" in data["amenities"][0]

    def test_restaurant_detail_includes_category(self, restaurant_with_category_and_amenities):
        """Test that RestaurantDetailSerializer includes category with translations."""
        serializer = RestaurantDetailSerializer(restaurant_with_category_and_amenities)
        data = serializer.data

        assert "category" in data
        assert data["category"] is not None
        assert "translations" in data["category"]

    def test_restaurant_detail_includes_amenities(self, restaurant_with_category_and_amenities):
        """Test that RestaurantDetailSerializer includes amenities with translations."""
        serializer = RestaurantDetailSerializer(restaurant_with_category_and_amenities)
        data = serializer.data

        assert "amenities" in data
        assert len(data["amenities"]) == 1
        assert "translations" in data["amenities"][0]

    def test_restaurant_without_category(self, restaurant):
        """Test serializer when restaurant has no category."""
        serializer = RestaurantListSerializer(restaurant)
        data = serializer.data

        assert data["category"] is None

    def test_restaurant_without_amenities(self, restaurant):
        """Test serializer when restaurant has no amenities."""
        serializer = RestaurantListSerializer(restaurant)
        data = serializer.data

        assert data["amenities"] == []

    def test_multiple_amenities_serialization(self, restaurant, create_amenity):
        """Test serialization of restaurant with multiple amenities."""
        terrace = create_amenity(name="Terrace", slug="terrace", icon="deck")
        wifi = create_amenity(name="WiFi", slug="wifi", icon="wifi")

        restaurant.amenities.add(terrace, wifi)

        serializer = RestaurantListSerializer(restaurant)
        data = serializer.data

        assert len(data["amenities"]) == 2
        slugs = [a["slug"] for a in data["amenities"]]
        assert "terrace" in slugs
        assert "wifi" in slugs
