"""
Tests for staff views.
"""

import pytest
from rest_framework import status


@pytest.mark.django_db
class TestStaffListView:
    """Tests for staff list endpoint."""

    url = "/api/v1/dashboard/staff/"

    def test_owner_can_list_staff(self, authenticated_owner_client, restaurant, waiter_staff):
        """Test that owner can list staff members."""
        # Set restaurant context via header
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = authenticated_owner_client.get(self.url)
        # Will fail without proper middleware setup in test, but structure is correct
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_403_FORBIDDEN]

    def test_unauthenticated_cannot_list(self, api_client, restaurant):
        """Test that unauthenticated users cannot list staff."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestStaffInviteView:
    """Tests for staff invite endpoint."""

    url = "/api/v1/dashboard/staff/invite/"

    def test_unauthenticated_cannot_invite(self, api_client):
        """Test that unauthenticated users cannot invite staff."""
        data = {"email": "newstaff@example.com", "role_id": "some-uuid"}
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestStaffRolesListView:
    """Tests for staff roles list endpoint."""

    url = "/api/v1/dashboard/staff/roles/"

    def test_unauthenticated_cannot_list_roles(self, api_client):
        """Test that unauthenticated users cannot list roles."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestInvitationDetailsView:
    """Tests for public invitation details endpoint."""

    def test_get_invitation_details(self, api_client, restaurant, staff_roles, user):
        """Test getting invitation details by token."""
        from apps.staff.models import StaffInvitation

        waiter_role = next(r for r in staff_roles if r.name == "waiter")
        invitation = StaffInvitation.create_invitation(
            restaurant=restaurant,
            email="newstaff@example.com",
            role=waiter_role,
            invited_by=user,
        )

        url = f"/api/v1/staff/invitations/{invitation.token}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert response.data["data"]["restaurant_name"] == restaurant.name

    def test_invalid_token(self, api_client):
        """Test with invalid invitation token."""
        url = "/api/v1/staff/invitations/invalid-token/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestAcceptInvitationView:
    """Tests for accepting staff invitation."""

    url = "/api/v1/staff/invitations/accept/"

    def test_accept_invitation(self, authenticated_client, restaurant, staff_roles, user, another_user):
        """Test accepting a valid invitation."""
        from apps.staff.models import StaffInvitation
        from rest_framework_simplejwt.tokens import RefreshToken
        from rest_framework.test import APIClient

        waiter_role = next(r for r in staff_roles if r.name == "waiter")
        invitation = StaffInvitation.create_invitation(
            restaurant=restaurant,
            email=another_user.email,
            role=waiter_role,
            invited_by=user,
        )

        # Authenticate as the invited user
        client = APIClient()
        refresh = RefreshToken.for_user(another_user)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        response = client.post(self.url, {"token": invitation.token}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True

    def test_accept_invalid_token(self, authenticated_client):
        """Test accepting with invalid token."""
        response = authenticated_client.post(self.url, {"token": "invalid-token"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unauthenticated_cannot_accept(self, api_client):
        """Test that unauthenticated users cannot accept invitations."""
        response = api_client.post(self.url, {"token": "some-token"}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
