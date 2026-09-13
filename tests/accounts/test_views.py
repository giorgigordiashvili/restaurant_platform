"""
Tests for accounts app views (API endpoints).
"""

from rest_framework import status

import pytest

from apps.accounts.models import User


@pytest.mark.django_db
class TestRegisterView:
    """Tests for user registration endpoint."""

    url = "/api/v1/auth/register/"

    def test_register_success(self, api_client, user_data):
        """Test successful user registration."""
        response = api_client.post(self.url, user_data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["success"] is True
        assert "data" in response.data
        assert "user" in response.data["data"]
        assert "tokens" in response.data["data"]
        assert "access" in response.data["data"]["tokens"]
        assert "refresh" in response.data["data"]["tokens"]

    def test_register_creates_user(self, api_client, user_data):
        """Test registration creates user in database."""
        response = api_client.post(self.url, user_data, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert User.objects.filter(email=user_data["email"].lower()).exists()

    def test_register_creates_profile(self, api_client, user_data):
        """Test registration creates user profile."""
        api_client.post(self.url, user_data, format="json")

        user = User.objects.get(email=user_data["email"].lower())
        assert hasattr(user, "profile")
        assert user.profile is not None

    def test_register_duplicate_email(self, api_client, user_data, user, api_envelope):
        """Test registration fails with duplicate email."""
        user_data["email"] = user.email
        response = api_client.post(self.url, user_data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        # The envelope carries stable codes so clients can show localized copy.
        assert response.data["success"] is False
        assert response.data["error"]["codes"] == {"email": ["email_taken"]}
        assert "already exists" in response.data["error"]["message"]
        assert "email" in response.data["error"]["details"]

    def test_register_weak_password_codes(self, api_client, user_data, api_envelope):
        user_data["password"] = user_data["password_confirm"] = "12345678"
        response = api_client.post(self.url, user_data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        codes = response.data["error"]["codes"]["password"]
        assert "password_too_common" in codes or "password_entirely_numeric" in codes

    def test_register_password_mismatch(self, api_client, user_data):
        """Test registration fails when passwords don't match."""
        user_data["password_confirm"] = "DifferentPassword!"
        response = api_client.post(self.url, user_data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_weak_password(self, api_client, user_data):
        """Test registration fails with weak password."""
        user_data["password"] = "123"
        user_data["password_confirm"] = "123"
        response = api_client.post(self.url, user_data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_missing_email(self, api_client, user_data):
        """Test registration fails without email."""
        del user_data["email"]
        response = api_client.post(self.url, user_data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_invalid_email(self, api_client, user_data):
        """Test registration fails with invalid email."""
        user_data["email"] = "invalid-email"
        response = api_client.post(self.url, user_data, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestLoginView:
    """Tests for user login endpoint."""

    url = "/api/v1/auth/login/"

    def test_login_success(self, api_client, user):
        """Test successful login."""
        response = api_client.post(self.url, {"email": user.email, "password": "TestPassword123!"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data
        assert "refresh" in response.data
        assert "user" in response.data

    def test_login_returns_user_data(self, api_client, user):
        """Test login returns user data."""
        response = api_client.post(self.url, {"email": user.email, "password": "TestPassword123!"}, format="json")

        assert response.data["user"]["email"] == user.email
        assert response.data["user"]["id"] == str(user.id)

    def test_login_wrong_password(self, api_client, user):
        """Test login fails with wrong password."""
        response = api_client.post(self.url, {"email": user.email, "password": "WrongPassword!"}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_login_nonexistent_user(self, api_client):
        """Test login fails with nonexistent user."""
        response = api_client.post(
            self.url, {"email": "nonexistent@example.com", "password": "SomePassword123!"}, format="json"
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_login_locked_account(self, api_client, user, api_envelope):
        """Test login fails for locked account."""
        # Lock the account
        for _ in range(5):
            user.increment_failed_login()

        response = api_client.post(self.url, {"email": user.email, "password": "TestPassword123!"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"]["codes"] == {"detail": ["account_locked"]}
        assert "locked" in response.data["error"]["message"]

    def test_login_wrong_password_envelope(self, api_client, user, api_envelope):
        response = api_client.post(self.url, {"email": user.email, "password": "nope"}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.data["error"]["code"] == "no_active_account"

    def test_login_case_insensitive_email(self, api_client, user):
        """Test login works with different email case."""
        response = api_client.post(
            self.url, {"email": user.email.upper(), "password": "TestPassword123!"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestLogoutView:
    """Tests for user logout endpoint."""

    url = "/api/v1/auth/logout/"

    def test_logout_success(self, authenticated_client, user_tokens):
        """Test successful logout."""
        response = authenticated_client.post(self.url, {"refresh": user_tokens["refresh"]}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True

    def test_logout_unauthenticated(self, api_client):
        """Test logout fails without authentication."""
        response = api_client.post(self.url, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_logout_invalidates_refresh_token(self, authenticated_client, user_tokens, api_client):
        """Test logout invalidates refresh token."""
        # Logout
        authenticated_client.post(self.url, {"refresh": user_tokens["refresh"]}, format="json")

        # Try to refresh with blacklisted token
        response = api_client.post("/api/v1/auth/token/refresh/", {"refresh": user_tokens["refresh"]}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestTokenRefreshView:
    """Tests for token refresh endpoint."""

    url = "/api/v1/auth/token/refresh/"

    def test_refresh_success(self, api_client, user_tokens):
        """Test successful token refresh."""
        response = api_client.post(self.url, {"refresh": user_tokens["refresh"]}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.data

    def test_refresh_invalid_token(self, api_client):
        """Test refresh fails with invalid token."""
        response = api_client.post(self.url, {"refresh": "invalid-token"}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestCurrentUserView:
    """Tests for current user endpoint."""

    url = "/api/v1/users/me/"

    def test_get_current_user(self, authenticated_client, user):
        """Test getting current user profile."""
        response = authenticated_client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert response.data["data"]["email"] == user.email

    def test_get_current_user_unauthenticated(self, api_client):
        """Test getting current user fails without auth."""
        response = api_client.get(self.url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_update_current_user(self, authenticated_client, user):
        """Test updating current user profile."""
        response = authenticated_client.patch(self.url, {"first_name": "Updated", "last_name": "Name"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["data"]["first_name"] == "Updated"
        assert response.data["data"]["last_name"] == "Name"

        user.refresh_from_db()
        assert user.first_name == "Updated"

    def test_update_preferred_language(self, authenticated_client, user):
        """Test updating preferred language."""
        response = authenticated_client.patch(self.url, {"preferred_language": "ru"}, format="json")

        assert response.status_code == status.HTTP_200_OK

        user.refresh_from_db()
        assert user.preferred_language == "ru"

    def test_update_profile_nested(self, authenticated_client, user):
        """Test updating nested profile data."""
        response = authenticated_client.patch(self.url, {"profile": {"email_notifications": False}}, format="json")

        assert response.status_code == status.HTTP_200_OK

        user.refresh_from_db()
        assert user.profile.email_notifications is False


@pytest.mark.django_db
class TestChangePasswordView:
    """Tests for password change endpoint."""

    url = "/api/v1/auth/password/change/"

    def test_change_password_success(self, authenticated_client, user):
        """Test successful password change."""
        response = authenticated_client.post(
            self.url,
            {
                "old_password": "TestPassword123!",
                "new_password": "NewPassword456!",
                "new_password_confirm": "NewPassword456!",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True

        user.refresh_from_db()
        assert user.check_password("NewPassword456!")

    def test_change_password_wrong_old(self, authenticated_client):
        """Test password change fails with wrong old password."""
        response = authenticated_client.post(
            self.url,
            {
                "old_password": "WrongPassword!",
                "new_password": "NewPassword456!",
                "new_password_confirm": "NewPassword456!",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_change_password_mismatch(self, authenticated_client):
        """Test password change fails when new passwords don't match."""
        response = authenticated_client.post(
            self.url,
            {
                "old_password": "TestPassword123!",
                "new_password": "NewPassword456!",
                "new_password_confirm": "DifferentPassword!",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_change_password_unauthenticated(self, api_client):
        """Test password change fails without authentication."""
        response = api_client.post(
            self.url,
            {
                "old_password": "TestPassword123!",
                "new_password": "NewPassword456!",
                "new_password_confirm": "NewPassword456!",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestPasswordResetRequestView:
    """Tests for password reset request endpoint."""

    url = "/api/v1/auth/password/reset/"

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear cache to reset throttling between tests."""
        from django.core.cache import cache

        cache.clear()
        yield
        cache.clear()

    def test_reset_request_existing_email(self, api_client, user):
        """Test password reset request for existing email."""
        response = api_client.post(self.url, {"email": user.email}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True

    def test_reset_request_nonexistent_email(self, api_client):
        """Test password reset request for nonexistent email still returns success."""
        # This is intentional to prevent email enumeration
        response = api_client.post(self.url, {"email": "nonexistent@example.com"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True


@pytest.mark.django_db
class TestDeleteAccountView:
    """Tests for account deletion endpoint."""

    url = "/api/v1/users/me/delete/"

    def test_delete_account_success(self, authenticated_client, user):
        """Test successful account deletion (soft delete)."""
        response = authenticated_client.delete(self.url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True

        user.refresh_from_db()
        assert user.is_active is False

    def test_delete_account_unauthenticated(self, api_client):
        """Test account deletion fails without authentication."""
        response = api_client.delete(self.url)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestEmailCheckView:
    url = "/api/v1/auth/email-check/"

    def test_unknown_email(self, api_client):
        response = api_client.post(self.url, {"email": "Nobody@Example.com"}, format="json")
        assert response.status_code == 200
        assert response.data["data"] == {"exists": False, "has_restaurants": False}

    def test_existing_customer_and_owner(self, api_client, user, restaurant):
        response = api_client.post(self.url, {"email": user.email.upper()}, format="json")
        assert response.data["data"] == {"exists": True, "has_restaurants": True}

    def test_invalid_email(self, api_client, api_envelope):
        response = api_client.post(self.url, {"email": "not-an-email"}, format="json")
        assert response.status_code == 400
        assert response.data["error"]["codes"] == {"email": ["invalid"]}


@pytest.mark.django_db
class TestSameEmailBothRoles:
    """One identity: a customer account can go on to own a restaurant and vice versa."""

    def test_customer_then_restaurant(self, api_client, user_data, api_envelope):
        r = api_client.post("/api/v1/auth/register/", user_data, format="json")
        assert r.status_code == 201
        # The restaurant signup form hits register again -> told the email exists...
        r2 = api_client.post("/api/v1/auth/register/", user_data, format="json")
        assert r2.data["error"]["codes"] == {"email": ["email_taken"]}
        # ...then signs in with the same credentials and creates the restaurant.
        login = api_client.post(
            "/api/v1/auth/login/", {"email": user_data["email"], "password": user_data["password"]}, format="json"
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        created = api_client.post("/api/v1/restaurants/create/", {"name": "My Place"}, format="json")
        assert created.status_code == 201 and created.data["data"]["slug"] == "my-place"
        user = User.objects.get(email=user_data["email"].lower())
        assert user.is_staff and user.owned_restaurants.count() == 1
        assert user.owned_restaurants.get().setup_completed_at is None
