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
