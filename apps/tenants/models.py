"""
Restaurant (tenant) models for multi-tenant restaurant platform.
"""

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from parler.models import TranslatableModel, TranslatedFields

from apps.core.models import TimeStampedModel
from apps.core.utils.validators import phone_validator


class RestaurantCategory(TranslatableModel, TimeStampedModel):
    """
    Category for restaurants (e.g., Italian, Georgian, Fast Food, Cafe).
    Managed by superadmins, selected when creating a restaurant.
    Supports translations for name and description.
    """

    translations = TranslatedFields(
        name=models.CharField(max_length=100),
        description=models.TextField(blank=True),
    )
    slug = models.SlugField(max_length=100, unique=True)
    icon = models.CharField(
        max_length=50,
        blank=True,
        help_text="Material icon name (e.g., 'restaurant', 'local_cafe', 'fastfood')",
    )
    image = models.ImageField(
        upload_to="restaurant_categories/",
        blank=True,
        null=True,
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "restaurant_categories"
        ordering = ["display_order"]
        verbose_name = _("Restaurant Category")
        verbose_name_plural = _("Restaurant Categories")

    def __str__(self):
        return self.safe_translation_getter("name", default=f"Category {self.pk}")

    def save(self, *args, **kwargs):
        if not self.slug:
            # Try to get the name from the default language for slug
            name = self.safe_translation_getter("name", default="")
            if name:
                self.slug = slugify(name)
        super().save(*args, **kwargs)

    @property
    def restaurants_count(self) -> int:
        """Return count of active restaurants in this category."""
        return self.restaurants.filter(is_active=True).count()


class Amenity(TranslatableModel, TimeStampedModel):
    """
    Amenities/features that restaurants can have (e.g., Terrace, Live Music, WiFi).
    Managed by superadmins, selected by restaurant owners.
    Supports translations for name and description.
    """

    translations = TranslatedFields(
        name=models.CharField(max_length=100),
        description=models.TextField(blank=True),
    )
    slug = models.SlugField(max_length=100, unique=True)
    icon = models.CharField(
        max_length=50,
        blank=True,
        help_text="Material icon name (e.g., 'deck', 'music_note', 'wifi')",
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "amenities"
        ordering = ["display_order"]
        verbose_name = _("Amenity")
        verbose_name_plural = _("Amenities")

    def __str__(self):
        return self.safe_translation_getter("name", default=f"Amenity {self.pk}")

    def save(self, *args, **kwargs):
        if not self.slug:
            name = self.safe_translation_getter("name", default="")
            if name:
                self.slug = slugify(name)
        super().save(*args, **kwargs)


class Restaurant(TimeStampedModel):
    """
    Restaurant model representing a tenant in the multi-tenant system.
    Each restaurant has its own menu, staff, orders, etc.
    """

    CURRENCY_CHOICES = [
        ("GEL", "Georgian Lari"),
        ("USD", "US Dollar"),
        ("EUR", "Euro"),
    ]

    TIMEZONE_CHOICES = [
        ("Asia/Tbilisi", "Tbilisi (GMT+4)"),
        ("Europe/London", "London (GMT)"),
        ("Europe/Berlin", "Berlin (GMT+1)"),
        ("America/New_York", "New York (GMT-5)"),
    ]

    # Basic info
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True, db_index=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    # Category
    category = models.ForeignKey(
        RestaurantCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="restaurants",
        help_text="Restaurant category (e.g., Italian, Fast Food)",
    )

    # Amenities
    amenities = models.ManyToManyField(
        Amenity,
        blank=True,
        related_name="restaurants",
        help_text="Amenities available at the restaurant",
    )

    # Owner
    owner = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="owned_restaurants",
    )

    # Contact info
    email = models.EmailField(blank=True)
    phone = models.CharField(
        max_length=20,
        blank=True,
        validators=[phone_validator],
    )
    website = models.URLField(blank=True)

    # Address
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, default="Georgia")

    # Location (for map display and search)
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )

    # Branding
    logo = models.ImageField(
        upload_to="restaurants/logos/",
        blank=True,
        null=True,
    )
    cover_image = models.ImageField(
        upload_to="restaurants/covers/",
        blank=True,
        null=True,
    )
    primary_color = models.CharField(
        max_length=7,
        default="#000000",
        validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Enter a valid hex color")],
    )
    secondary_color = models.CharField(
        max_length=7,
        default="#FFFFFF",
        validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Enter a valid hex color")],
    )

    # Settings
    default_currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default="GEL",
    )
    timezone = models.CharField(
        max_length=50,
        choices=TIMEZONE_CHOICES,
        default="Asia/Tbilisi",
    )
    default_language = models.CharField(
        max_length=2,
        choices=[("ka", "Georgian"), ("en", "English"), ("ru", "Russian")],
        default="ka",
    )

    # Features
    accepts_remote_orders = models.BooleanField(
        default=True,
        help_text="Allow customers to place orders without being at the restaurant",
    )
    accepts_reservations = models.BooleanField(
        default=True,
        help_text="Allow customers to make reservations",
    )
    accepts_takeaway = models.BooleanField(
        default=True,
        help_text="Allow takeaway orders",
    )

    # Tax settings
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Tax percentage to apply to orders",
    )
    service_charge = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Service charge percentage",
    )

    # Cached statistics (updated periodically)
    average_rating = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    total_reviews = models.PositiveIntegerField(default=0)
    total_orders = models.PositiveIntegerField(default=0)

    # Ordering settings
    minimum_order_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Minimum order amount for remote orders",
    )
    average_preparation_time = models.PositiveIntegerField(
        default=30,
        help_text="Average order preparation time in minutes",
    )

    class Meta:
        db_table = "restaurants"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["city"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
            # Ensure uniqueness
            base_slug = self.slug
            counter = 1
            while Restaurant.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{base_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)

    @property
    def full_address(self) -> str:
        """Return formatted full address."""
        parts = [self.address, self.city, self.postal_code, self.country]
        return ", ".join(p for p in parts if p)

    @property
    def is_open_now(self) -> bool:
        """Check if restaurant is currently open based on operating hours."""
        from django.utils import timezone

        now = timezone.localtime()
        day = now.weekday()

        try:
            hours = self.operating_hours.get(day_of_week=day)
            if hours.is_closed:
                return False
            return hours.open_time <= now.time() <= hours.close_time
        except RestaurantHours.DoesNotExist:
            return False

    def get_today_hours(self):
        """Get today's operating hours."""
        from django.utils import timezone

        day = timezone.localtime().weekday()
        try:
            return self.operating_hours.get(day_of_week=day)
        except RestaurantHours.DoesNotExist:
            return None


class RestaurantHours(TimeStampedModel):
    """
    Operating hours for a restaurant on a specific day of the week.
    """

    DAY_CHOICES = [
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
        (5, "Saturday"),
        (6, "Sunday"),
    ]

    restaurant = models.ForeignKey(
        Restaurant,
        on_delete=models.CASCADE,
        related_name="operating_hours",
    )
    day_of_week = models.PositiveSmallIntegerField(choices=DAY_CHOICES)
    open_time = models.TimeField()
    close_time = models.TimeField()
    is_closed = models.BooleanField(
        default=False,
        help_text="Mark as closed for this day (overrides open/close times)",
    )

    class Meta:
        db_table = "restaurant_hours"
        unique_together = ["restaurant", "day_of_week"]
        ordering = ["day_of_week"]

    def __str__(self):
        if self.is_closed:
            return f"{self.restaurant.name} - {self.get_day_of_week_display()} (Closed)"
        return f"{self.restaurant.name} - {self.get_day_of_week_display()} ({self.open_time} - {self.close_time})"

    @classmethod
    def create_default_hours(cls, restaurant):
        """Create default operating hours for a new restaurant (9 AM - 10 PM, 7 days)."""
        from datetime import time

        hours = []
        for day in range(7):
            hour, created = cls.objects.get_or_create(
                restaurant=restaurant,
                day_of_week=day,
                defaults={
                    "open_time": time(9, 0),
                    "close_time": time(22, 0),
                    "is_closed": False,
                },
            )
            hours.append(hour)
        return hours
