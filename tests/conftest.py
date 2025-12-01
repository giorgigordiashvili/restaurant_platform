"""
Pytest configuration and fixtures for the restaurant platform tests.
"""
import pytest
from django.test import Client
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.fixture
def api_client():
    """Return an API client for testing."""
    return APIClient()


@pytest.fixture
def user_data():
    """Return basic user data for registration."""
    return {
        'email': 'testuser@example.com',
        'password': 'TestPassword123!',
        'password_confirm': 'TestPassword123!',
        'first_name': 'Test',
        'last_name': 'User',
        'preferred_language': 'en',
    }


@pytest.fixture
def create_user(db):
    """Factory fixture to create users."""
    from apps.accounts.models import User

    def _create_user(
        email='user@example.com',
        password='TestPassword123!',
        first_name='Test',
        last_name='User',
        **kwargs
    ):
        user = User.objects.create_user(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            **kwargs
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
    return create_user(
        email='another@example.com',
        first_name='Another',
        last_name='User'
    )


@pytest.fixture
def authenticated_client(api_client, user):
    """Return an authenticated API client."""
    refresh = RefreshToken.for_user(user)
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')
    return api_client


@pytest.fixture
def user_tokens(user):
    """Return access and refresh tokens for a user."""
    refresh = RefreshToken.for_user(user)
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
    }


@pytest.fixture
def admin_user(create_user):
    """Create and return an admin user."""
    return create_user(
        email='admin@example.com',
        first_name='Admin',
        last_name='User',
        is_staff=True,
        is_superuser=True
    )


@pytest.fixture
def authenticated_admin_client(api_client, admin_user):
    """Return an authenticated API client with admin privileges."""
    refresh = RefreshToken.for_user(admin_user)
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')
    return api_client
