"""
Tests for accounts app models.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

import pytest

from apps.accounts.models import User, UserProfile


@pytest.mark.django_db
class TestUserModel:
    """Tests for the User model."""

    def test_create_user(self, create_user):
        """Test creating a user with email."""
        user = create_user(email="newuser@example.com")
        assert user.email == "newuser@example.com"
        assert user.check_password("TestPassword123!")
        assert user.is_active
        assert not user.is_staff
        assert not user.is_superuser

    def test_create_user_email_normalized(self, create_user):
        """Test that email domain is normalized on user creation."""
        user = create_user(email="Test@EXAMPLE.COM")
        # Django's normalize_email lowercases the domain part only
        assert user.email == "Test@example.com"

    def test_create_user_without_email_raises_error(self):
        """Test creating a user without email raises error."""
        with pytest.raises(ValueError):
            User.objects.create_user(email="", password="test123")

    def test_create_superuser(self):
        """Test creating a superuser."""
        user = User.objects.create_superuser(email="admin@example.com", password="AdminPass123!")
        assert user.is_staff
        assert user.is_superuser
        assert user.is_active

    def test_user_str_method(self, user):
        """Test user string representation."""
        assert str(user) == user.email

    def test_user_full_name_with_names(self, create_user):
        """Test full_name property with first and last name."""
        user = create_user(first_name="John", last_name="Doe")
        assert user.full_name == "John Doe"

    def test_user_full_name_without_names(self, create_user):
        """Test full_name property without names returns email."""
        user = create_user(first_name="", last_name="")
        assert user.full_name == user.email

    def test_username_auto_generated(self, create_user):
        """Test username is auto-generated from email."""
        user = create_user(email="johndoe@example.com")
        assert user.username == "johndoe"

    def test_username_uniqueness_handling(self, create_user):
        """Test username uniqueness with duplicate email prefixes."""
        user1 = create_user(email="test@example.com")
        user2 = create_user(email="test@different.com")
        assert user1.username == "test"
        assert user2.username.startswith("test")
        assert user1.username != user2.username

    def test_user_uuid_primary_key(self, user):
        """Test that user has UUID primary key."""
        import uuid

        assert isinstance(user.id, uuid.UUID)

    def test_account_locking_after_failed_attempts(self, user):
        """Test account gets locked after 5 failed attempts."""
        for _ in range(5):
            user.increment_failed_login()

        assert user.is_account_locked()
        assert user.failed_login_attempts == 5
        assert user.locked_until is not None

    def test_account_not_locked_after_few_attempts(self, user):
        """Test account is not locked after fewer than 5 attempts."""
        for _ in range(4):
            user.increment_failed_login()

        assert not user.is_account_locked()
        assert user.failed_login_attempts == 4

    def test_reset_failed_login(self, user):
        """Test resetting failed login counter."""
        for _ in range(3):
            user.increment_failed_login()

        user.reset_failed_login()

        assert user.failed_login_attempts == 0
        assert user.locked_until is None

    def test_account_lock_expires(self, user):
        """Test account lock expires after 30 minutes."""
        for _ in range(5):
            user.increment_failed_login()

        # Manually set locked_until to past
        user.locked_until = timezone.now() - timedelta(minutes=1)
        user.save()

        assert not user.is_account_locked()

    def test_update_last_login_ip(self, user):
        """Test updating last login IP."""
        user.update_last_login_ip("192.168.1.1")
        assert user.last_login_ip == "192.168.1.1"

    def test_preferred_language_choices(self, create_user):
        """Test preferred language choices."""
        user_ka = create_user(email="ka@example.com", preferred_language="ka")
        user_en = create_user(email="en@example.com", preferred_language="en")
        user_ru = create_user(email="ru@example.com", preferred_language="ru")

        assert user_ka.preferred_language == "ka"
        assert user_en.preferred_language == "en"
        assert user_ru.preferred_language == "ru"

    def test_phone_number_validation_valid(self, create_user):
        """Test valid phone number formats."""
        user = create_user(phone_number="+995599123456")
        user.full_clean()  # Should not raise
        assert user.phone_number == "+995599123456"

    def test_timestamps_auto_set(self, user):
        """Test created_at and updated_at are automatically set."""
        assert user.created_at is not None
        assert user.updated_at is not None


@pytest.mark.django_db
class TestUserProfileModel:
    """Tests for the UserProfile model."""

    def test_profile_created_with_user(self, user):
        """Test profile is created when user is created."""
        # Profile should be created by signal
        assert hasattr(user, "profile")
        assert user.profile is not None

    def test_profile_str_method(self, user):
        """Test profile string representation."""
        assert str(user.profile) == f"Profile of {user.email}"

    def test_profile_default_values(self, user):
        """Test profile default values."""
        profile = user.profile
        assert profile.loyalty_points == 0
        assert profile.total_orders == 0
        assert profile.total_spent == 0
        assert profile.email_notifications is True
        assert profile.sms_notifications is False
        assert profile.push_notifications is True
        assert profile.preferences == {}

    def test_add_loyalty_points(self, user):
        """Test adding loyalty points."""
        user.profile.add_loyalty_points(100)
        assert user.profile.loyalty_points == 100

        user.profile.add_loyalty_points(50)
        assert user.profile.loyalty_points == 150

    def test_increment_order_stats(self, user):
        """Test incrementing order statistics."""
        from decimal import Decimal

        user.profile.increment_order_stats(Decimal("25.50"))
        assert user.profile.total_orders == 1
        assert user.profile.total_spent == Decimal("25.50")

        user.profile.increment_order_stats(Decimal("30.00"))
        assert user.profile.total_orders == 2
        assert user.profile.total_spent == Decimal("55.50")

    def test_profile_preferences_json(self, user):
        """Test profile preferences JSON field."""
        user.profile.preferences = {"dietary": ["vegetarian", "no_nuts"], "notifications": {"email_frequency": "daily"}}
        user.profile.save()

        user.profile.refresh_from_db()
        assert user.profile.preferences["dietary"] == ["vegetarian", "no_nuts"]

    def test_profile_cascade_delete(self, user):
        """Test profile is deleted when user is deleted."""
        profile_id = user.profile.id
        user.delete()

        assert not UserProfile.objects.filter(id=profile_id).exists()
