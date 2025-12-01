"""
Menu models with multi-language support via django-parler.
"""

from django.core.validators import MinValueValidator
from django.db import models

from parler.models import TranslatableModel, TranslatedFields

from apps.core.models import TimeStampedModel


class MenuCategory(TranslatableModel, TimeStampedModel):
    """
    Category for organizing menu items (e.g., Appetizers, Main Courses, Desserts).
    Supports translations for name and description.
    """

    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="menu_categories",
    )
    translations = TranslatedFields(
        name=models.CharField(max_length=100),
        description=models.TextField(blank=True),
    )
    image = models.ImageField(
        upload_to="menu/categories/",
        blank=True,
        null=True,
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "menu_categories"
        ordering = ["display_order", "created_at"]
        verbose_name_plural = "Menu Categories"

    def __str__(self):
        return self.safe_translation_getter("name", default=f"Category {self.pk}")

    @property
    def items_count(self) -> int:
        """Return count of active items in this category."""
        return self.items.filter(is_available=True).count()


class MenuItem(TranslatableModel, TimeStampedModel):
    """
    Individual menu item with translations, pricing, and dietary info.
    """

    PREPARATION_STATION_CHOICES = [
        ("kitchen", "Kitchen"),
        ("bar", "Bar"),
        ("both", "Both"),
    ]

    # Common allergens
    ALLERGEN_CHOICES = [
        ("gluten", "Gluten"),
        ("dairy", "Dairy"),
        ("eggs", "Eggs"),
        ("fish", "Fish"),
        ("shellfish", "Shellfish"),
        ("tree_nuts", "Tree Nuts"),
        ("peanuts", "Peanuts"),
        ("soy", "Soy"),
        ("sesame", "Sesame"),
    ]

    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="menu_items",
    )
    category = models.ForeignKey(
        MenuCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="items",
    )
    translations = TranslatedFields(
        name=models.CharField(max_length=200),
        description=models.TextField(blank=True),
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    image = models.ImageField(
        upload_to="menu/items/",
        blank=True,
        null=True,
    )
    is_available = models.BooleanField(default=True)
    is_featured = models.BooleanField(
        default=False,
        help_text="Featured items appear prominently in the menu",
    )
    display_order = models.PositiveIntegerField(default=0)

    # Preparation
    preparation_time_minutes = models.PositiveIntegerField(
        default=15,
        help_text="Estimated preparation time in minutes",
    )
    preparation_station = models.CharField(
        max_length=10,
        choices=PREPARATION_STATION_CHOICES,
        default="kitchen",
    )

    # Dietary information
    calories = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Calorie count per serving",
    )
    allergens = models.JSONField(
        default=list,
        blank=True,
        help_text="List of allergen codes (e.g., ['gluten', 'dairy'])",
    )
    is_vegetarian = models.BooleanField(default=False)
    is_vegan = models.BooleanField(default=False)
    is_gluten_free = models.BooleanField(default=False)
    is_spicy = models.BooleanField(default=False)
    spicy_level = models.PositiveSmallIntegerField(
        default=0,
        help_text="Spicy level 0-5 (0 = not spicy)",
    )

    # Inventory (optional)
    track_inventory = models.BooleanField(default=False)
    stock_quantity = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "menu_items"
        ordering = ["display_order", "created_at"]
        indexes = [
            models.Index(fields=["restaurant", "is_available"]),
            models.Index(fields=["category", "is_available"]),
        ]

    def __str__(self):
        return self.safe_translation_getter("name", default=f"Item {self.pk}")

    @property
    def is_in_stock(self) -> bool:
        """Check if item is in stock (if inventory tracking is enabled)."""
        if not self.track_inventory:
            return True
        return self.stock_quantity > 0

    def get_dietary_tags(self) -> list:
        """Return list of dietary tags for display."""
        tags = []
        if self.is_vegetarian:
            tags.append("vegetarian")
        if self.is_vegan:
            tags.append("vegan")
        if self.is_gluten_free:
            tags.append("gluten_free")
        if self.is_spicy:
            tags.append("spicy")
        return tags


class ModifierGroup(TranslatableModel, TimeStampedModel):
    """
    Group of modifiers for customizing menu items (e.g., Size, Toppings).
    """

    SELECTION_TYPE_CHOICES = [
        ("single", "Single Selection"),
        ("multiple", "Multiple Selection"),
    ]

    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="modifier_groups",
    )
    translations = TranslatedFields(
        name=models.CharField(max_length=100),
        description=models.TextField(blank=True),
    )
    selection_type = models.CharField(
        max_length=10,
        choices=SELECTION_TYPE_CHOICES,
        default="single",
    )
    min_selections = models.PositiveSmallIntegerField(
        default=0,
        help_text="Minimum number of selections required",
    )
    max_selections = models.PositiveSmallIntegerField(
        default=1,
        help_text="Maximum number of selections allowed",
    )
    is_required = models.BooleanField(
        default=False,
        help_text="Customer must make a selection from this group",
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "modifier_groups"
        ordering = ["display_order", "created_at"]

    def __str__(self):
        return self.safe_translation_getter("name", default=f"Modifier Group {self.pk}")


class Modifier(TranslatableModel, TimeStampedModel):
    """
    Individual modifier option within a group (e.g., Small, Medium, Large).
    """

    group = models.ForeignKey(
        ModifierGroup,
        on_delete=models.CASCADE,
        related_name="modifiers",
    )
    translations = TranslatedFields(
        name=models.CharField(max_length=100),
    )
    price_adjustment = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Price adjustment (can be positive or negative)",
    )
    is_available = models.BooleanField(default=True)
    is_default = models.BooleanField(
        default=False,
        help_text="Pre-selected by default",
    )
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "modifiers"
        ordering = ["display_order", "created_at"]

    def __str__(self):
        name = self.safe_translation_getter("name", default=f"Modifier {self.pk}")
        if self.price_adjustment:
            return f"{name} (+{self.price_adjustment})"
        return name


class MenuItemModifierGroup(TimeStampedModel):
    """
    Links menu items to modifier groups.
    Allows different items to have different modifier groups.
    """

    menu_item = models.ForeignKey(
        MenuItem,
        on_delete=models.CASCADE,
        related_name="modifier_groups_link",
    )
    modifier_group = models.ForeignKey(
        ModifierGroup,
        on_delete=models.CASCADE,
        related_name="menu_items_link",
    )
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "menu_item_modifier_groups"
        unique_together = ["menu_item", "modifier_group"]
        ordering = ["display_order"]

    def __str__(self):
        return f"{self.menu_item} - {self.modifier_group}"
