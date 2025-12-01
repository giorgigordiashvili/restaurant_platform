"""
Tests for accounts app serializers.
"""

from django.test import RequestFactory

from rest_framework.request import Request

import pytest

from apps.accounts.serializers import (
    ChangePasswordSerializer,
    CustomTokenObtainPairSerializer,
    PasswordResetRequestSerializer,
    UserRegistrationSerializer,
    UserSerializer,
    UserUpdateSerializer,
)


@pytest.mark.django_db
class TestUserSerializer:
    """Tests for UserSerializer."""

    def test_serializer_fields(self, user):
        """Test serializer returns expected fields."""
        serializer = UserSerializer(user)
        data = serializer.data

        assert "id" in data
        assert "email" in data
        assert "first_name" in data
        assert "last_name" in data
        assert "full_name" in data
        assert "phone_number" in data
        assert "preferred_language" in data
        assert "created_at" in data
        assert "profile" in data

    def test_serializer_read_only_fields(self, user):
        """Test read-only fields cannot be updated."""
        serializer = UserSerializer(user)
        assert "id" in serializer.Meta.read_only_fields
        assert "email" in serializer.Meta.read_only_fields
        assert "created_at" in serializer.Meta.read_only_fields

    def test_password_not_in_output(self, user):
        """Test password is not included in serialized output."""
        serializer = UserSerializer(user)
        assert "password" not in serializer.data


@pytest.mark.django_db
class TestUserRegistrationSerializer:
    """Tests for UserRegistrationSerializer."""

    def test_valid_registration_data(self, user_data):
        """Test registration with valid data."""
        serializer = UserRegistrationSerializer(data=user_data)
        assert serializer.is_valid(), serializer.errors

    def test_password_mismatch(self, user_data):
        """Test registration fails when passwords don't match."""
        user_data["password_confirm"] = "DifferentPassword123!"
        serializer = UserRegistrationSerializer(data=user_data)
        assert not serializer.is_valid()
        assert "password_confirm" in serializer.errors

    def test_duplicate_email(self, user_data, user):
        """Test registration fails with duplicate email."""
        user_data["email"] = user.email
        serializer = UserRegistrationSerializer(data=user_data)
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_duplicate_email_case_insensitive(self, user_data, create_user):
        """Test duplicate email check is case insensitive."""
        create_user(email="test@example.com")
        user_data["email"] = "TEST@EXAMPLE.COM"
        serializer = UserRegistrationSerializer(data=user_data)
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_email_normalized(self, user_data):
        """Test email is normalized to lowercase."""
        user_data["email"] = "Test@EXAMPLE.com"
        serializer = UserRegistrationSerializer(data=user_data)
        assert serializer.is_valid()
        assert serializer.validated_data["email"] == "test@example.com"

    def test_weak_password_rejected(self, user_data):
        """Test weak password is rejected."""
        user_data["password"] = "weak"
        user_data["password_confirm"] = "weak"
        serializer = UserRegistrationSerializer(data=user_data)
        assert not serializer.is_valid()
        assert "password" in serializer.errors

    def test_user_created_successfully(self, user_data):
        """Test user is created with valid data."""
        serializer = UserRegistrationSerializer(data=user_data)
        assert serializer.is_valid()
        user = serializer.save()

        assert user.email == user_data["email"].lower()
        assert user.first_name == user_data["first_name"]
        assert user.check_password(user_data["password"])

    def test_password_not_returned(self, user_data):
        """Test password fields are write-only."""
        serializer = UserRegistrationSerializer(data=user_data)
        assert serializer.is_valid()
        user = serializer.save()

        output_serializer = UserRegistrationSerializer(user)
        assert "password" not in output_serializer.data
        assert "password_confirm" not in output_serializer.data


@pytest.mark.django_db
class TestUserUpdateSerializer:
    """Tests for UserUpdateSerializer."""

    def test_update_user_fields(self, user):
        """Test updating user fields."""
        data = {
            "first_name": "Updated",
            "last_name": "Name",
        }
        serializer = UserUpdateSerializer(user, data=data, partial=True)
        assert serializer.is_valid()
        updated_user = serializer.save()

        assert updated_user.first_name == "Updated"
        assert updated_user.last_name == "Name"

    def test_update_preferred_language(self, user):
        """Test updating preferred language."""
        data = {"preferred_language": "ru"}
        serializer = UserUpdateSerializer(user, data=data, partial=True)
        assert serializer.is_valid()
        updated_user = serializer.save()

        assert updated_user.preferred_language == "ru"

    def test_update_profile_nested(self, user):
        """Test updating nested profile data."""
        data = {
            "first_name": "John",
            "profile": {
                "email_notifications": False,
                "sms_notifications": True,
            },
        }
        serializer = UserUpdateSerializer(user, data=data, partial=True)
        assert serializer.is_valid()
        updated_user = serializer.save()

        assert updated_user.first_name == "John"
        assert updated_user.profile.email_notifications is False
        assert updated_user.profile.sms_notifications is True


@pytest.mark.django_db
class TestChangePasswordSerializer:
    """Tests for ChangePasswordSerializer."""

    def test_valid_password_change(self, user):
        """Test password change with valid data."""

        # Create a mock request with the user
        class MockRequest:
            def __init__(self, user):
                self.user = user

        mock_request = MockRequest(user)

        data = {
            "old_password": "TestPassword123!",
            "new_password": "NewPassword456!",
            "new_password_confirm": "NewPassword456!",
        }
        serializer = ChangePasswordSerializer(data=data, context={"request": mock_request})
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        user.refresh_from_db()
        assert user.check_password("NewPassword456!")

    def test_wrong_old_password(self, user):
        """Test password change fails with wrong old password."""

        class MockRequest:
            def __init__(self, user):
                self.user = user

        mock_request = MockRequest(user)

        data = {
            "old_password": "WrongPassword123!",
            "new_password": "NewPassword456!",
            "new_password_confirm": "NewPassword456!",
        }
        serializer = ChangePasswordSerializer(data=data, context={"request": mock_request})
        assert not serializer.is_valid()
        assert "old_password" in serializer.errors

    def test_new_passwords_mismatch(self, user):
        """Test password change fails when new passwords don't match."""

        class MockRequest:
            def __init__(self, user):
                self.user = user

        mock_request = MockRequest(user)

        data = {
            "old_password": "TestPassword123!",
            "new_password": "NewPassword456!",
            "new_password_confirm": "DifferentPassword789!",
        }
        serializer = ChangePasswordSerializer(data=data, context={"request": mock_request})
        assert not serializer.is_valid()
        assert "new_password_confirm" in serializer.errors


@pytest.mark.django_db
class TestPasswordResetRequestSerializer:
    """Tests for PasswordResetRequestSerializer."""

    def test_valid_email(self):
        """Test valid email passes validation."""
        serializer = PasswordResetRequestSerializer(data={"email": "test@example.com"})
        assert serializer.is_valid()

    def test_invalid_email(self):
        """Test invalid email fails validation."""
        serializer = PasswordResetRequestSerializer(data={"email": "invalid"})
        assert not serializer.is_valid()
        assert "email" in serializer.errors

    def test_email_normalized(self):
        """Test email is normalized to lowercase."""
        serializer = PasswordResetRequestSerializer(data={"email": "TEST@EXAMPLE.com"})
        assert serializer.is_valid()
        assert serializer.validated_data["email"] == "test@example.com"


@pytest.mark.django_db
class TestCustomTokenObtainPairSerializer:
    """Tests for CustomTokenObtainPairSerializer."""

    def test_valid_credentials_returns_tokens(self, user):
        """Test valid credentials return tokens and user data."""
        factory = RequestFactory()
        request = factory.post("/")

        serializer = CustomTokenObtainPairSerializer(
            data={"email": user.email, "password": "TestPassword123!"}, context={"request": Request(request)}
        )
        assert serializer.is_valid(), serializer.errors

        data = serializer.validated_data
        assert "access" in data
        assert "refresh" in data
        assert "user" in data
        assert data["user"]["email"] == user.email

    def test_invalid_password_fails(self, user):
        """Test invalid password fails authentication."""
        factory = RequestFactory()
        request = factory.post("/")

        serializer = CustomTokenObtainPairSerializer(
            data={"email": user.email, "password": "WrongPassword!"}, context={"request": Request(request)}
        )
        # is_valid() raises exception for auth failures
        with pytest.raises(Exception):
            serializer.is_valid(raise_exception=True)

    def test_locked_account_rejected(self, user):
        """Test locked account is rejected."""
        # Lock the account
        for _ in range(5):
            user.increment_failed_login()

        factory = RequestFactory()
        request = factory.post("/")

        serializer = CustomTokenObtainPairSerializer(
            data={"email": user.email, "password": "TestPassword123!"}, context={"request": Request(request)}
        )
        assert not serializer.is_valid()
        assert "detail" in serializer.errors

    def test_failed_login_increments_counter(self, user):
        """Test failed login increments failed login counter."""
        factory = RequestFactory()
        request = factory.post("/")

        serializer = CustomTokenObtainPairSerializer(
            data={"email": user.email, "password": "WrongPassword!"}, context={"request": Request(request)}
        )
        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            pass  # Expected to fail

        user.refresh_from_db()
        assert user.failed_login_attempts == 1

    def test_successful_login_resets_counter(self, create_user):
        """Test successful login resets failed login counter."""
        user = create_user()
        user.failed_login_attempts = 3
        user.save()

        factory = RequestFactory()
        request = factory.post("/")

        serializer = CustomTokenObtainPairSerializer(
            data={"email": user.email, "password": "TestPassword123!"}, context={"request": Request(request)}
        )
        assert serializer.is_valid()

        user.refresh_from_db()
        assert user.failed_login_attempts == 0
