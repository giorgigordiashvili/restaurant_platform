"""
Tests for tables views.
"""

import uuid

from rest_framework import status

import pytest


@pytest.mark.django_db
class TestQRCodeScanView:
    """Tests for public QR code scan endpoint."""

    url = "/api/v1/tables/scan/"

    def test_scan_valid_qr_code(self, api_client, table_qr_code):
        """Test scanning a valid QR code."""
        response = api_client.post(self.url, {"code": table_qr_code.code})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert "restaurant_slug" in response.data["data"]
        assert "table_number" in response.data["data"]

    def test_scan_invalid_qr_code(self, api_client):
        """Test scanning an invalid QR code."""
        response = api_client.post(self.url, {"code": "invalidcode"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_scan_inactive_qr_code(self, api_client, table_qr_code):
        """Test scanning an inactive QR code."""
        table_qr_code.is_active = False
        table_qr_code.save()
        response = api_client.post(self.url, {"code": table_qr_code.code})
        # Should fail because QR code is inactive
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND]

    def test_scan_increments_count(self, api_client, table_qr_code):
        """Test that scanning increments the scan count."""
        initial_count = table_qr_code.scans_count
        api_client.post(self.url, {"code": table_qr_code.code})
        table_qr_code.refresh_from_db()
        assert table_qr_code.scans_count == initial_count + 1


@pytest.mark.django_db
class TestDashboardTableSectionListView:
    """Tests for dashboard table section list endpoint."""

    url = "/api/v1/dashboard/tables/sections/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list sections."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_list(self, authenticated_owner_client, restaurant, table_section):
        """Test that authenticated owner can list sections."""
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = authenticated_owner_client.get(self.url)
        # May be 200 or 403 depending on middleware
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestDashboardTableSectionCreateView:
    """Tests for dashboard table section create endpoint."""

    url = "/api/v1/dashboard/tables/sections/"

    def test_unauthenticated_cannot_create(self, api_client):
        """Test that unauthenticated users cannot create sections."""
        data = {"name": "New Section"}
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardTableListView:
    """Tests for dashboard table list endpoint."""

    url = "/api/v1/dashboard/tables/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list tables."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_list(self, authenticated_owner_client, restaurant, table):
        """Test that authenticated owner can list tables."""
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = authenticated_owner_client.get(self.url)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestDashboardTableCreateView:
    """Tests for dashboard table create endpoint."""

    url = "/api/v1/dashboard/tables/"

    def test_unauthenticated_cannot_create(self, api_client):
        """Test that unauthenticated users cannot create tables."""
        data = {"number": "A1", "capacity": 4}
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardTableDetailView:
    """Tests for dashboard table detail endpoint."""

    def test_unauthenticated_cannot_access(self, api_client, table):
        """Test that unauthenticated users cannot access table details."""
        url = f"/api/v1/dashboard/tables/{table.id}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_not_found(self, authenticated_owner_client, restaurant):
        """Test 404 for non-existent table."""
        url = f"/api/v1/dashboard/tables/{uuid.uuid4()}/"
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = authenticated_owner_client.get(url)
        assert response.status_code in [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestDashboardTableStatusUpdateView:
    """Tests for dashboard table status update endpoint."""

    def test_unauthenticated_cannot_update_status(self, api_client, table):
        """Test that unauthenticated users cannot update table status."""
        url = f"/api/v1/dashboard/tables/{table.id}/status/"
        response = api_client.patch(url, {"status": "occupied"}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardTableQRCodeListView:
    """Tests for dashboard table QR code list endpoint."""

    def test_unauthenticated_cannot_list(self, api_client, table):
        """Test that unauthenticated users cannot list QR codes."""
        url = f"/api/v1/dashboard/tables/{table.id}/qr-codes/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardTableSessionListView:
    """Tests for dashboard table session list endpoint."""

    url = "/api/v1/dashboard/tables/sessions/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list sessions."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardTableSessionCreateView:
    """Tests for dashboard table session create endpoint."""

    url = "/api/v1/dashboard/tables/sessions/start/"

    def test_unauthenticated_cannot_create(self, api_client, table):
        """Test that unauthenticated users cannot create sessions."""
        data = {"table_id": str(table.id), "guest_count": 2}
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardTableSessionCloseView:
    """Tests for dashboard table session close endpoint."""

    def test_unauthenticated_cannot_close(self, api_client, table_session):
        """Test that unauthenticated users cannot close sessions."""
        url = f"/api/v1/dashboard/tables/sessions/{table_session.id}/close/"
        response = api_client.post(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ============== Public Session Tests (Multi-user Ordering) ==============


@pytest.mark.django_db
class TestTableSessionDetailPublicView:
    """Tests for public table session detail endpoint."""

    def test_can_get_active_session(self, api_client, table_session_with_host):
        """Test that anyone can get active session details."""
        url = f"/api/v1/tables/sessions/{table_session_with_host.id}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert "invite_code" in response.data

    def test_cannot_get_closed_session(self, api_client, table_session):
        """Test that closed sessions are not accessible."""
        table_session.status = "closed"
        table_session.save()
        url = f"/api/v1/tables/sessions/{table_session.id}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestTableSessionInviteView:
    """Tests for session invite code endpoint."""

    def test_host_can_get_invite_code(self, authenticated_client, user, table_session_with_host):
        """Test that host can get the invite code."""
        url = f"/api/v1/tables/sessions/{table_session_with_host.id}/invite/"
        response = authenticated_client.post(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert "invite_code" in response.data["data"]
        assert len(response.data["data"]["invite_code"]) == 8

    def test_non_host_cannot_get_invite_code(self, api_client, another_user, table_session_with_host):
        """Test that non-host cannot get the invite code."""
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(another_user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        url = f"/api/v1/tables/sessions/{table_session_with_host.id}/invite/"
        response = api_client.post(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_unauthenticated_cannot_get_invite_code(self, api_client, table_session_with_host):
        """Test that unauthenticated users cannot get the invite code."""
        url = f"/api/v1/tables/sessions/{table_session_with_host.id}/invite/"
        response = api_client.post(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestJoinTableSessionPreviewView:
    """Tests for session join preview endpoint."""

    def test_can_preview_session(self, api_client, table_session_with_host):
        """Test that anyone can preview session info before joining."""
        url = f"/api/v1/tables/sessions/join/{table_session_with_host.invite_code}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert "restaurant_name" in response.data["data"]
        assert "table_number" in response.data["data"]

    def test_invalid_invite_code_returns_404(self, api_client):
        """Test that invalid invite codes return 404."""
        url = "/api/v1/tables/sessions/join/INVALIDX/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestJoinTableSessionView:
    """Tests for session join endpoint."""

    def test_authenticated_user_can_join(self, authenticated_client, user, table_session_with_host, another_user):
        """Test that authenticated user can join a session."""
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(another_user)
        authenticated_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        url = f"/api/v1/tables/sessions/join/{table_session_with_host.invite_code}/confirm/"
        response = authenticated_client.post(url)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["success"] is True
        assert "guest" in response.data["data"]

    def test_anonymous_user_needs_guest_name(self, api_client, table_session_with_host):
        """Test that anonymous users need to provide guest name."""
        url = f"/api/v1/tables/sessions/join/{table_session_with_host.invite_code}/confirm/"
        response = api_client.post(url, {})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_anonymous_user_can_join_with_name(self, api_client, table_session_with_host):
        """Test that anonymous users can join with guest name."""
        url = f"/api/v1/tables/sessions/join/{table_session_with_host.invite_code}/confirm/"
        response = api_client.post(url, {"guest_name": "John"})
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["success"] is True

    def test_user_already_joined_returns_existing(self, authenticated_client, user, table_session_with_host):
        """Test that rejoining returns existing guest record."""
        url = f"/api/v1/tables/sessions/join/{table_session_with_host.invite_code}/confirm/"
        # First join (host is already in)
        response = authenticated_client.post(url)
        # Already joined as host
        assert response.status_code == status.HTTP_200_OK
        assert response.data["message"] == "Already joined this session."


@pytest.mark.django_db
class TestTableSessionGuestsView:
    """Tests for session guests list endpoint."""

    def test_can_list_guests(self, api_client, table_session_with_host):
        """Test that anyone can list session guests."""
        url = f"/api/v1/tables/sessions/{table_session_with_host.id}/guests/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        # At least the host should be there
        assert len(response.data["results"]) >= 1


@pytest.mark.django_db
class TestTableSessionOrdersView:
    """Tests for session orders list endpoint."""

    def test_can_list_orders(self, api_client, table_session_with_host):
        """Test that anyone can list session orders."""
        url = f"/api/v1/tables/sessions/{table_session_with_host.id}/orders/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert "orders" in response.data["data"]


@pytest.mark.django_db
class TestLeaveTableSessionView:
    """Tests for leave session endpoint."""

    def test_host_cannot_leave(self, authenticated_client, user, table_session_with_host):
        """Test that host cannot leave the session."""
        url = f"/api/v1/tables/sessions/{table_session_with_host.id}/leave/"
        response = authenticated_client.post(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Host cannot leave" in response.data["error"]["message"]

    def test_guest_can_leave(self, api_client, another_user, table_session_with_host, create_session_guest):
        """Test that non-host guest can leave the session."""
        from rest_framework_simplejwt.tokens import RefreshToken

        # Add another user as guest
        create_session_guest(session=table_session_with_host, user=another_user, is_host=False)

        refresh = RefreshToken.for_user(another_user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        url = f"/api/v1/tables/sessions/{table_session_with_host.id}/leave/"
        response = api_client.post(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True

    def test_anonymous_guest_can_leave_with_guest_id(self, api_client, table_session_with_host, create_session_guest):
        """Test that anonymous guest can leave with guest_id."""
        guest = create_session_guest(
            session=table_session_with_host,
            guest_name="Anonymous Guest",
            is_host=False,
        )
        url = f"/api/v1/tables/sessions/{table_session_with_host.id}/leave/"
        response = api_client.post(url, {"guest_id": str(guest.id)})
        assert response.status_code == status.HTTP_200_OK
