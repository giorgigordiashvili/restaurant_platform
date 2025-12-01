"""
Pytest configuration and fixtures for the restaurant platform tests.
"""

from rest_framework.test import APIClient

import pytest
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.fixture
def api_client():
    """Return an API client for testing."""
    return APIClient()


@pytest.fixture
def user_data():
    """Return basic user data for registration."""
    return {
        "email": "testuser@example.com",
        "password": "TestPassword123!",
        "password_confirm": "TestPassword123!",
        "first_name": "Test",
        "last_name": "User",
        "preferred_language": "en",
    }


@pytest.fixture
def create_user(db):
    """Factory fixture to create users."""
    from apps.accounts.models import User

    def _create_user(
        email="user@example.com", password="TestPassword123!", first_name="Test", last_name="User", **kwargs
    ):
        user = User.objects.create_user(
            email=email, password=password, first_name=first_name, last_name=last_name, **kwargs
        )
        return user

    return _create_user


@pytest.fixture
def user(create_user):
    """Create and return a test user."""
    return create_user()


@pytest.fixture
def another_user(create_user):
    """Create and return another test user."""
    return create_user(email="another@example.com", first_name="Another", last_name="User")


@pytest.fixture
def authenticated_client(api_client, user):
    """Return an authenticated API client."""
    refresh = RefreshToken.for_user(user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


@pytest.fixture
def user_tokens(user):
    """Return access and refresh tokens for a user."""
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


@pytest.fixture
def admin_user(create_user):
    """Create and return an admin user."""
    return create_user(
        email="admin@example.com", first_name="Admin", last_name="User", is_staff=True, is_superuser=True
    )


@pytest.fixture
def authenticated_admin_client(api_client, admin_user):
    """Return an authenticated API client with admin privileges."""
    refresh = RefreshToken.for_user(admin_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


# ============== Restaurant Fixtures ==============


@pytest.fixture
def create_restaurant(db):
    """Factory fixture to create restaurants."""
    from apps.tenants.models import Restaurant

    def _create_restaurant(owner, name="Test Restaurant", slug="test-restaurant", **kwargs):
        restaurant = Restaurant.objects.create(owner=owner, name=name, slug=slug, is_active=True, **kwargs)
        return restaurant

    return _create_restaurant


@pytest.fixture
def restaurant(create_restaurant, user):
    """Create and return a test restaurant owned by the test user."""
    return create_restaurant(owner=user)


@pytest.fixture
def another_restaurant(create_restaurant, another_user):
    """Create and return another test restaurant."""
    return create_restaurant(
        owner=another_user,
        name="Another Restaurant",
        slug="another-restaurant",
    )


@pytest.fixture
def restaurant_with_hours(restaurant):
    """Create restaurant with default operating hours."""
    from apps.tenants.models import RestaurantHours

    RestaurantHours.create_default_hours(restaurant)
    return restaurant


# ============== Staff Fixtures ==============


@pytest.fixture
def create_staff_role(db):
    """Factory fixture to create staff roles."""
    from apps.staff.models import StaffRole

    def _create_staff_role(restaurant, name="waiter", **kwargs):
        role = StaffRole.objects.create(restaurant=restaurant, name=name, **kwargs)
        return role

    return _create_staff_role


@pytest.fixture
def staff_roles(restaurant):
    """Create default staff roles for a restaurant."""
    from apps.staff.models import StaffRole

    return StaffRole.create_default_roles(restaurant)


@pytest.fixture
def create_staff_member(db):
    """Factory fixture to create staff members."""
    from apps.staff.models import StaffMember

    def _create_staff_member(user, restaurant, role, **kwargs):
        member = StaffMember.objects.create(user=user, restaurant=restaurant, role=role, **kwargs)
        return member

    return _create_staff_member


@pytest.fixture
def waiter_user(create_user):
    """Create a user for waiter role."""
    return create_user(email="waiter@example.com", first_name="Waiter", last_name="Staff")


@pytest.fixture
def waiter_staff(create_staff_member, waiter_user, restaurant, staff_roles):
    """Create a waiter staff member."""
    waiter_role = next(r for r in staff_roles if r.name == "waiter")
    return create_staff_member(
        user=waiter_user,
        restaurant=restaurant,
        role=waiter_role,
    )


@pytest.fixture
def manager_user(create_user):
    """Create a user for manager role."""
    return create_user(email="manager@example.com", first_name="Manager", last_name="Staff")


@pytest.fixture
def manager_staff(create_staff_member, manager_user, restaurant, staff_roles):
    """Create a manager staff member."""
    manager_role = next(r for r in staff_roles if r.name == "manager")
    return create_staff_member(
        user=manager_user,
        restaurant=restaurant,
        role=manager_role,
    )


@pytest.fixture
def authenticated_owner_client(api_client, user):
    """Return an authenticated client for restaurant owner."""
    refresh = RefreshToken.for_user(user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


@pytest.fixture
def authenticated_manager_client(api_client, manager_user):
    """Return an authenticated client for manager."""
    refresh = RefreshToken.for_user(manager_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


@pytest.fixture
def authenticated_waiter_client(api_client, waiter_user):
    """Return an authenticated client for waiter."""
    refresh = RefreshToken.for_user(waiter_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


# ============== Menu Fixtures ==============


@pytest.fixture
def create_menu_category(db):
    """Factory fixture to create menu categories."""
    from apps.menu.models import MenuCategory

    def _create_category(restaurant, name="Test Category", **kwargs):
        category = MenuCategory.objects.create(restaurant=restaurant, **kwargs)
        # Set translation
        category.set_current_language("en")
        category.name = name
        category.save()
        return category

    return _create_category


@pytest.fixture
def menu_category(create_menu_category, restaurant):
    """Create a test menu category."""
    return create_menu_category(restaurant=restaurant, name="Appetizers")


@pytest.fixture
def create_menu_item(db):
    """Factory fixture to create menu items."""
    from apps.menu.models import MenuItem
    from decimal import Decimal

    def _create_item(restaurant, category=None, name="Test Item", price=Decimal("10.00"), **kwargs):
        item = MenuItem.objects.create(restaurant=restaurant, category=category, price=price, **kwargs)
        # Set translation
        item.set_current_language("en")
        item.name = name
        item.save()
        return item

    return _create_item


@pytest.fixture
def menu_item(create_menu_item, restaurant, menu_category):
    """Create a test menu item."""
    return create_menu_item(
        restaurant=restaurant,
        category=menu_category,
        name="Test Dish",
    )


@pytest.fixture
def create_modifier_group(db):
    """Factory fixture to create modifier groups."""
    from apps.menu.models import ModifierGroup

    def _create_group(restaurant, name="Test Modifiers", **kwargs):
        group = ModifierGroup.objects.create(restaurant=restaurant, **kwargs)
        # Set translation
        group.set_current_language("en")
        group.name = name
        group.save()
        return group

    return _create_group


@pytest.fixture
def modifier_group(create_modifier_group, restaurant):
    """Create a test modifier group."""
    return create_modifier_group(restaurant=restaurant, name="Size")
