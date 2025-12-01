"""
Tests for menu models.
"""

import pytest
from decimal import Decimal

from apps.menu.models import MenuCategory, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup


@pytest.mark.django_db
class TestMenuCategoryModel:
    """Tests for MenuCategory model."""

    def test_create_category(self, restaurant):
        """Test creating a menu category."""
        category = MenuCategory.objects.create(restaurant=restaurant)
        category.set_current_language("en")
        category.name = "Appetizers"
        category.description = "Start your meal"
        category.save()

        assert category.name == "Appetizers"
        assert category.restaurant == restaurant
        assert category.is_active is True

    def test_category_str(self, menu_category):
        """Test category string representation."""
        assert str(menu_category) == "Appetizers"

    def test_items_count(self, restaurant, menu_category, create_menu_item):
        """Test items count property."""
        create_menu_item(restaurant=restaurant, category=menu_category, name="Item 1")
        create_menu_item(restaurant=restaurant, category=menu_category, name="Item 2")
        create_menu_item(restaurant=restaurant, category=menu_category, name="Item 3", is_available=False)

        assert menu_category.items_count == 2  # Only available items


@pytest.mark.django_db
class TestMenuItemModel:
    """Tests for MenuItem model."""

    def test_create_item(self, restaurant, menu_category):
        """Test creating a menu item."""
        item = MenuItem.objects.create(
            restaurant=restaurant,
            category=menu_category,
            price=Decimal("15.00"),
        )
        item.set_current_language("en")
        item.name = "Grilled Chicken"
        item.description = "Delicious grilled chicken"
        item.save()

        assert item.name == "Grilled Chicken"
        assert item.price == Decimal("15.00")
        assert item.is_available is True

    def test_item_str(self, menu_item):
        """Test item string representation."""
        assert str(menu_item) == "Test Dish"

    def test_dietary_tags(self, restaurant, menu_category):
        """Test dietary tags property."""
        item = MenuItem.objects.create(
            restaurant=restaurant,
            category=menu_category,
            price=Decimal("10.00"),
            is_vegetarian=True,
            is_vegan=True,
            is_gluten_free=True,
        )
        item.set_current_language("en")
        item.name = "Vegan Dish"
        item.save()

        tags = item.get_dietary_tags()
        assert "vegetarian" in tags
        assert "vegan" in tags
        assert "gluten_free" in tags

    def test_is_in_stock_without_tracking(self, menu_item):
        """Test is_in_stock when not tracking inventory."""
        assert menu_item.is_in_stock is True

    def test_is_in_stock_with_tracking(self, restaurant, menu_category):
        """Test is_in_stock when tracking inventory."""
        item = MenuItem.objects.create(
            restaurant=restaurant,
            category=menu_category,
            price=Decimal("10.00"),
            track_inventory=True,
            stock_quantity=0,
        )
        item.set_current_language("en")
        item.name = "Limited Item"
        item.save()

        assert item.is_in_stock is False

        item.stock_quantity = 10
        item.save()
        assert item.is_in_stock is True


@pytest.mark.django_db
class TestModifierGroupModel:
    """Tests for ModifierGroup model."""

    def test_create_modifier_group(self, restaurant):
        """Test creating a modifier group."""
        group = ModifierGroup.objects.create(
            restaurant=restaurant,
            selection_type="single",
            min_selections=1,
            max_selections=1,
            is_required=True,
        )
        group.set_current_language("en")
        group.name = "Size"
        group.save()

        assert group.name == "Size"
        assert group.selection_type == "single"
        assert group.is_required is True

    def test_modifier_group_str(self, modifier_group):
        """Test modifier group string representation."""
        assert str(modifier_group) == "Size"


@pytest.mark.django_db
class TestModifierModel:
    """Tests for Modifier model."""

    def test_create_modifier(self, modifier_group):
        """Test creating a modifier."""
        modifier = Modifier.objects.create(
            group=modifier_group,
            price_adjustment=Decimal("2.00"),
        )
        modifier.set_current_language("en")
        modifier.name = "Large"
        modifier.save()

        assert modifier.name == "Large"
        assert modifier.price_adjustment == Decimal("2.00")

    def test_modifier_str_with_price(self, modifier_group):
        """Test modifier string with price adjustment."""
        modifier = Modifier.objects.create(
            group=modifier_group,
            price_adjustment=Decimal("2.00"),
        )
        modifier.set_current_language("en")
        modifier.name = "Large"
        modifier.save()

        assert "Large" in str(modifier)
        assert "2.00" in str(modifier)

    def test_modifier_str_without_price(self, modifier_group):
        """Test modifier string without price adjustment."""
        modifier = Modifier.objects.create(
            group=modifier_group,
            price_adjustment=Decimal("0"),
        )
        modifier.set_current_language("en")
        modifier.name = "Regular"
        modifier.save()

        assert str(modifier) == "Regular"


@pytest.mark.django_db
class TestMenuItemModifierGroupModel:
    """Tests for MenuItemModifierGroup linking model."""

    def test_link_item_to_modifier_group(self, menu_item, modifier_group):
        """Test linking a menu item to a modifier group."""
        link = MenuItemModifierGroup.objects.create(
            menu_item=menu_item,
            modifier_group=modifier_group,
            display_order=0,
        )
        assert link.menu_item == menu_item
        assert link.modifier_group == modifier_group

    def test_unique_constraint(self, menu_item, modifier_group):
        """Test unique constraint on menu_item + modifier_group."""
        MenuItemModifierGroup.objects.create(
            menu_item=menu_item,
            modifier_group=modifier_group,
        )
        with pytest.raises(Exception):
            MenuItemModifierGroup.objects.create(
                menu_item=menu_item,
                modifier_group=modifier_group,  # Duplicate
            )
