"""
Tests for audit views.
"""

from rest_framework import status

import pytest

from apps.audit.models import AuditLog


@pytest.mark.django_db
class TestDashboardAuditLogListView:
    """Tests for dashboard audit log list endpoint."""

    url = "/api/v1/dashboard/audit/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list audit logs."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_owner_can_list(self, authenticated_owner_client, user, restaurant):
        """Test that authenticated owner can list audit logs."""
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="login",
        )
        response = authenticated_owner_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK

    def test_filter_by_action(self, authenticated_owner_client, user, restaurant):
        """Test filtering audit logs by action."""
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="login",
        )
        AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="logout",
        )
        response = authenticated_owner_client.get(self.url, {"action": "login"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 1


@pytest.mark.django_db
class TestDashboardAuditLogDetailView:
    """Tests for dashboard audit log detail endpoint."""

    def test_unauthenticated_cannot_access(self, api_client, user, restaurant):
        """Test that unauthenticated users cannot access audit log detail."""
        log = AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="login",
        )
        url = f"/api/v1/dashboard/audit/{log.id}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_owner_can_access(self, authenticated_owner_client, user, restaurant):
        """Test that authenticated owner can access audit log detail."""
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        log = AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="login",
            description="Test login",
        )
        url = f"/api/v1/dashboard/audit/{log.id}/"
        response = authenticated_owner_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "login"


@pytest.mark.django_db
class TestDashboardAuditLogStatsView:
    """Tests for dashboard audit log stats endpoint."""

    url = "/api/v1/dashboard/audit/stats/"

    def test_unauthenticated_cannot_access(self, api_client):
        """Test that unauthenticated users cannot access stats."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_owner_can_access(self, authenticated_owner_client, user, restaurant):
        """Test that authenticated owner can access stats."""
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="login",
        )
        response = authenticated_owner_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert "total_logs" in response.data
        assert "logs_today" in response.data
        assert "logs_by_action" in response.data


@pytest.mark.django_db
class TestDashboardAuditLogActionsView:
    """Tests for dashboard audit log actions endpoint."""

    url = "/api/v1/dashboard/audit/actions/"

    def test_unauthenticated_cannot_access(self, api_client):
        """Test that unauthenticated users cannot access actions."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_owner_can_access(self, authenticated_owner_client, restaurant):
        """Test that authenticated owner can access actions list."""
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = authenticated_owner_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.data, list)
        assert len(response.data) > 0
        assert "value" in response.data[0]
        assert "label" in response.data[0]


@pytest.mark.django_db
class TestDashboardAuditLogExportView:
    """Tests for dashboard audit log export endpoint."""

    url = "/api/v1/dashboard/audit/export/"

    def test_unauthenticated_cannot_export(self, api_client):
        """Test that unauthenticated users cannot export audit logs."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_owner_can_export(self, authenticated_owner_client, user, restaurant):
        """Test that authenticated owner can export audit logs."""
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="login",
        )
        response = authenticated_owner_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert "count" in response.data
        assert "data" in response.data
        assert "exported_at" in response.data


@pytest.mark.django_db
class TestAdminAuditLogListView:
    """Tests for admin audit log list endpoint."""

    url = "/api/v1/admin/audit/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list audit logs."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_non_staff_gets_empty_list(self, authenticated_client, user, restaurant):
        """Test that non-staff users get empty list."""
        AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="login",
        )
        response = authenticated_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0

    def test_staff_can_list_all(self, authenticated_admin_client, user, restaurant):
        """Test that staff can list all audit logs."""
        AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="login",
        )
        response = authenticated_admin_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] >= 1


@pytest.mark.django_db
class TestAdminAuditLogDetailView:
    """Tests for admin audit log detail endpoint."""

    def test_non_staff_cannot_access(self, authenticated_client, user, restaurant):
        """Test that non-staff users cannot access audit log detail."""
        log = AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="login",
        )
        url = f"/api/v1/admin/audit/{log.id}/"
        response = authenticated_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_staff_can_access(self, authenticated_admin_client, user, restaurant):
        """Test that staff can access audit log detail."""
        log = AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="login",
        )
        url = f"/api/v1/admin/audit/{log.id}/"
        response = authenticated_admin_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["action"] == "login"
