"""
Common validators for the restaurant platform.
"""
import re

import bleach
from django.core.validators import RegexValidator
from django.core.exceptions import ValidationError


# Phone number validator (supports international formats)
phone_validator = RegexValidator(
    regex=r'^\+?[1-9]\d{8,14}$',
    message="Phone number must be in format: '+999999999'. 9-15 digits allowed."
)

# Slug validator (for restaurant subdomains)
slug_validator = RegexValidator(
    regex=r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$',
    message="Slug must be lowercase alphanumeric with hyphens, 1-63 characters."
)

# Georgian phone number validator
georgian_phone_validator = RegexValidator(
    regex=r'^(\+995)?[0-9]{9}$',
    message="Georgian phone number must be 9 digits, optionally with +995 prefix."
)


def validate_hex_color(value):
    """Validate hex color code."""
    if not re.match(r'^#[0-9A-Fa-f]{6}$', value):
        raise ValidationError(
            f'{value} is not a valid hex color code. Use format: #RRGGBB'
        )


def validate_image_size(image, max_size_mb=5):
    """Validate image file size."""
    max_size_bytes = max_size_mb * 1024 * 1024
    if image.size > max_size_bytes:
        raise ValidationError(
            f'Image file size must be under {max_size_mb}MB. '
            f'Current size: {image.size / (1024 * 1024):.2f}MB'
        )


def validate_price(value):
    """Validate price is positive."""
    if value < 0:
        raise ValidationError('Price cannot be negative.')


def validate_percentage(value):
    """Validate percentage is between 0 and 100."""
    if not 0 <= value <= 100:
        raise ValidationError('Percentage must be between 0 and 100.')


def sanitize_html(value):
    """
    Sanitize HTML input to prevent XSS attacks.

    Args:
        value: The string to sanitize

    Returns:
        Sanitized string with only allowed tags
    """
    if not value:
        return value

    allowed_tags = ['b', 'i', 'u', 'strong', 'em', 'p', 'br', 'ul', 'ol', 'li']
    allowed_attrs = {}

    return bleach.clean(
        value,
        tags=allowed_tags,
        attributes=allowed_attrs,
        strip=True
    )


def validate_operating_hours(value):
    """
    Validate operating hours JSON structure.

    Expected format:
    {
        "0": {"open": "09:00", "close": "22:00", "is_closed": false},
        "1": {"open": "09:00", "close": "22:00", "is_closed": false},
        ...
    }
    """
    if not isinstance(value, dict):
        raise ValidationError('Operating hours must be a dictionary.')

    time_pattern = re.compile(r'^([01]?[0-9]|2[0-3]):[0-5][0-9]$')

    for day, hours in value.items():
        if not day.isdigit() or not 0 <= int(day) <= 6:
            raise ValidationError(f'Invalid day: {day}. Must be 0-6.')

        if not isinstance(hours, dict):
            raise ValidationError(f'Hours for day {day} must be a dictionary.')

        if hours.get('is_closed', False):
            continue

        open_time = hours.get('open')
        close_time = hours.get('close')

        if not open_time or not time_pattern.match(open_time):
            raise ValidationError(f'Invalid open time for day {day}.')

        if not close_time or not time_pattern.match(close_time):
            raise ValidationError(f'Invalid close time for day {day}.')


def validate_allergens(value):
    """
    Validate allergens list.

    Expected format: ["nuts", "dairy", "gluten"]
    """
    allowed_allergens = [
        'nuts', 'peanuts', 'dairy', 'eggs', 'gluten', 'wheat',
        'soy', 'fish', 'shellfish', 'sesame', 'mustard', 'celery',
        'lupin', 'molluscs', 'sulphites'
    ]

    if not isinstance(value, list):
        raise ValidationError('Allergens must be a list.')

    for allergen in value:
        if allergen.lower() not in allowed_allergens:
            raise ValidationError(
                f'Invalid allergen: {allergen}. '
                f'Allowed: {", ".join(allowed_allergens)}'
            )
