"""
Tests for core validators.
"""

from unittest.mock import Mock

from django.core.exceptions import ValidationError

import pytest

from apps.core.utils.validators import (
    georgian_phone_validator,
    phone_validator,
    sanitize_html,
    slug_validator,
    validate_allergens,
    validate_hex_color,
    validate_image_size,
    validate_operating_hours,
    validate_percentage,
    validate_price,
)


class TestPhoneValidator:
    """Tests for phone_validator."""

    def test_valid_international_format(self):
        """Test valid international phone numbers."""
        valid_numbers = [
            "+995599123456",
            "+12345678901",
            "+441onal234567890",  # Removing this - invalid format with letters
            "995599123456",
        ]
        # Filter out invalid entries for this test
        valid_numbers = ["+995599123456", "+12345678901", "995599123456"]
        for number in valid_numbers:
            try:
                phone_validator(number)
            except ValidationError:
                pytest.fail(f"Phone number {number} should be valid")

    def test_invalid_phone_numbers(self):
        """Test invalid phone numbers."""
        invalid_numbers = [
            "123",  # Too short
            "+0123456789",  # Starts with 0
            "abc123456789",  # Contains letters
            "",  # Empty
        ]
        for number in invalid_numbers:
            with pytest.raises(ValidationError):
                phone_validator(number)


class TestSlugValidator:
    """Tests for slug_validator."""

    def test_valid_slugs(self):
        """Test valid slug formats."""
        valid_slugs = [
            "myrestaurant",
            "my-restaurant",
            "restaurant123",
            "a",
            "my-cool-restaurant-2024",
        ]
        for slug in valid_slugs:
            try:
                slug_validator(slug)
            except ValidationError:
                pytest.fail(f"Slug {slug} should be valid")

    def test_invalid_slugs(self):
        """Test invalid slug formats."""
        invalid_slugs = [
            "MyRestaurant",  # Uppercase
            "-restaurant",  # Starts with hyphen
            "restaurant-",  # Ends with hyphen
            "my_restaurant",  # Contains underscore
            "my restaurant",  # Contains space
            "",  # Empty
        ]
        for slug in invalid_slugs:
            with pytest.raises(ValidationError):
                slug_validator(slug)


class TestGeorgianPhoneValidator:
    """Tests for georgian_phone_validator."""

    def test_valid_georgian_phones(self):
        """Test valid Georgian phone numbers."""
        valid_numbers = [
            "+995599123456",
            "599123456",
        ]
        for number in valid_numbers:
            try:
                georgian_phone_validator(number)
            except ValidationError:
                pytest.fail(f"Georgian phone {number} should be valid")

    def test_invalid_georgian_phones(self):
        """Test invalid Georgian phone numbers."""
        invalid_numbers = [
            "+99599123456",  # Wrong country code
            "12345678",  # Only 8 digits
            "+995599123456789",  # Too long
        ]
        for number in invalid_numbers:
            with pytest.raises(ValidationError):
                georgian_phone_validator(number)


class TestValidateHexColor:
    """Tests for validate_hex_color."""

    def test_valid_hex_colors(self):
        """Test valid hex color codes."""
        valid_colors = ["#FF0000", "#00ff00", "#0000FF", "#123ABC", "#abcdef"]
        for color in valid_colors:
            try:
                validate_hex_color(color)
            except ValidationError:
                pytest.fail(f"Color {color} should be valid")

    def test_invalid_hex_colors(self):
        """Test invalid hex color codes."""
        invalid_colors = [
            "FF0000",  # Missing #
            "#FFF",  # Too short
            "#GGGGGG",  # Invalid characters
            "#FF00000",  # Too long
            "",  # Empty
        ]
        for color in invalid_colors:
            with pytest.raises(ValidationError):
                validate_hex_color(color)


class TestValidateImageSize:
    """Tests for validate_image_size."""

    def test_valid_image_size(self):
        """Test image within size limit."""
        image = Mock()
        image.size = 1024 * 1024  # 1MB

        try:
            validate_image_size(image, max_size_mb=5)
        except ValidationError:
            pytest.fail("Image size should be valid")

    def test_image_too_large(self):
        """Test image exceeding size limit."""
        image = Mock()
        image.size = 10 * 1024 * 1024  # 10MB

        with pytest.raises(ValidationError):
            validate_image_size(image, max_size_mb=5)

    def test_custom_max_size(self):
        """Test custom max size limit."""
        image = Mock()
        image.size = 2 * 1024 * 1024  # 2MB

        with pytest.raises(ValidationError):
            validate_image_size(image, max_size_mb=1)


class TestValidatePrice:
    """Tests for validate_price."""

    def test_valid_prices(self):
        """Test valid price values."""
        valid_prices = [0, 1, 10.99, 100, 999.99]
        for price in valid_prices:
            try:
                validate_price(price)
            except ValidationError:
                pytest.fail(f"Price {price} should be valid")

    def test_negative_price(self):
        """Test negative price raises error."""
        with pytest.raises(ValidationError):
            validate_price(-1)


class TestValidatePercentage:
    """Tests for validate_percentage."""

    def test_valid_percentages(self):
        """Test valid percentage values."""
        valid_percentages = [0, 50, 100, 25.5]
        for percentage in valid_percentages:
            try:
                validate_percentage(percentage)
            except ValidationError:
                pytest.fail(f"Percentage {percentage} should be valid")

    def test_invalid_percentages(self):
        """Test invalid percentage values."""
        invalid_percentages = [-1, 101, -50, 150]
        for percentage in invalid_percentages:
            with pytest.raises(ValidationError):
                validate_percentage(percentage)


class TestSanitizeHtml:
    """Tests for sanitize_html."""

    def test_allowed_tags_preserved(self):
        """Test allowed HTML tags are preserved."""
        html = "<b>bold</b> <i>italic</i> <p>paragraph</p>"
        result = sanitize_html(html)
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result
        assert "<p>paragraph</p>" in result

    def test_dangerous_tags_removed(self):
        """Test dangerous HTML tags are stripped."""
        html = '<script>alert("xss")</script><b>safe</b>'
        result = sanitize_html(html)
        assert "<script>" not in result
        # Note: bleach with strip=True may keep text content of removed tags
        assert "<b>safe</b>" in result

    def test_dangerous_attributes_removed(self):
        """Test dangerous attributes are removed."""
        html = '<b onclick="alert()">text</b>'
        result = sanitize_html(html)
        assert "onclick" not in result
        assert "<b>text</b>" in result

    def test_empty_value_returned_as_is(self):
        """Test empty/None values are returned as-is."""
        assert sanitize_html("") == ""
        assert sanitize_html(None) is None


class TestValidateOperatingHours:
    """Tests for validate_operating_hours."""

    def test_valid_operating_hours(self):
        """Test valid operating hours structure."""
        valid_hours = {
            "0": {"open": "09:00", "close": "22:00", "is_closed": False},
            "1": {"open": "09:00", "close": "22:00", "is_closed": False},
            "2": {"open": "09:00", "close": "22:00", "is_closed": False},
            "3": {"open": "09:00", "close": "22:00", "is_closed": False},
            "4": {"open": "09:00", "close": "23:00", "is_closed": False},
            "5": {"open": "10:00", "close": "23:00", "is_closed": False},
            "6": {"open": "10:00", "close": "22:00", "is_closed": True},
        }
        try:
            validate_operating_hours(valid_hours)
        except ValidationError:
            pytest.fail("Operating hours should be valid")

    def test_closed_day_skips_time_validation(self):
        """Test closed day doesn't require time fields."""
        hours = {
            "0": {"is_closed": True},
        }
        try:
            validate_operating_hours(hours)
        except ValidationError:
            pytest.fail("Closed day should be valid without times")

    def test_invalid_day_number(self):
        """Test invalid day number raises error."""
        hours = {
            "7": {"open": "09:00", "close": "22:00", "is_closed": False},
        }
        with pytest.raises(ValidationError):
            validate_operating_hours(hours)

    def test_invalid_time_format(self):
        """Test invalid time format raises error."""
        hours = {
            "0": {"open": "9:00 AM", "close": "22:00", "is_closed": False},
        }
        with pytest.raises(ValidationError):
            validate_operating_hours(hours)

    def test_not_dict_raises_error(self):
        """Test non-dict value raises error."""
        with pytest.raises(ValidationError):
            validate_operating_hours("not a dict")


class TestValidateAllergens:
    """Tests for validate_allergens."""

    def test_valid_allergens(self):
        """Test valid allergen list."""
        valid_allergens = ["nuts", "dairy", "gluten"]
        try:
            validate_allergens(valid_allergens)
        except ValidationError:
            pytest.fail("Allergens should be valid")

    def test_empty_list_valid(self):
        """Test empty allergen list is valid."""
        try:
            validate_allergens([])
        except ValidationError:
            pytest.fail("Empty allergen list should be valid")

    def test_invalid_allergen(self):
        """Test invalid allergen raises error."""
        with pytest.raises(ValidationError):
            validate_allergens(["nuts", "invalid_allergen"])

    def test_not_list_raises_error(self):
        """Test non-list value raises error."""
        with pytest.raises(ValidationError):
            validate_allergens("nuts, dairy")

    def test_case_insensitive(self):
        """Test allergen validation is case insensitive."""
        try:
            validate_allergens(["NUTS", "Dairy", "GLUTEN"])
        except ValidationError:
            pytest.fail("Case insensitive allergens should be valid")
