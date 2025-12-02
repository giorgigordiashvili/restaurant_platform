"""
Tests for audit services.
"""

from unittest.mock import Mock

import pytest

from apps.audit.services import AuditLogService, log_action


@pytest.mark.django_db
class TestAuditLogService:
    """Tests for AuditLogService."""

    def test_log_basic(self, user, restaurant):
        """Test basic logging."""
        log = AuditLogService.log(
            action="login",
            user=user,
            restaurant=restaurant,
            description="User logged in",
        )
        assert log.action == "login"
        assert log.user == user
        assert log.restaurant == restaurant
        assert log.description == "User logged in"

    def test_log_with_request(self, user, restaurant):
        """Test logging with request object."""
        request = Mock(spec=["META", "method", "path", "user", "restaurant"])
        request.META = {
            "HTTP_USER_AGENT": "Test Browser",
            "REMOTE_ADDR": "127.0.0.1",
        }
        request.method = "POST"
        request.path = "/api/v1/test/"
        # Don't set request.user - pass user directly to avoid Mock auto-creation
        request.user = None
        request.restaurant = None

        log = AuditLogService.log(
            action="settings_update",
            request=request,
            user=user,
            restaurant=restaurant,
            description="Settings updated",
        )
        assert log.user == user
        assert log.restaurant == restaurant
        assert log.ip_address == "127.0.0.1"
        assert log.user_agent == "Test Browser"
        assert log.request_method == "POST"
        assert log.request_path == "/api/v1/test/"

    def test_log_with_x_forwarded_for(self, user):
        """Test IP extraction from X-Forwarded-For header."""
        request = Mock(spec=["META", "method", "path", "user", "restaurant"])
        request.META = {
            "HTTP_X_FORWARDED_FOR": "203.0.113.195, 70.41.3.18, 150.172.238.178",
            "REMOTE_ADDR": "127.0.0.1",
        }
        request.method = "GET"
        request.path = "/"
        request.user = None
        request.restaurant = None

        log = AuditLogService.log(
            action="login",
            request=request,
            user=user,
        )
        assert log.ip_address == "203.0.113.195"

    def test_log_login_success(self, user):
        """Test logging successful login."""
        request = Mock(spec=["META", "method", "path"])
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        request.method = "POST"
        request.path = "/api/v1/auth/login/"

        log = AuditLogService.log_login(request, user, success=True)
        assert log.action == "login"
        assert log.user == user
        assert log.response_status == 200

    def test_log_login_failure(self):
        """Test logging failed login."""
        request = Mock(spec=["META", "method", "path"])
        request.META = {"REMOTE_ADDR": "192.168.1.100"}
        request.method = "POST"
        request.path = "/api/v1/auth/login/"

        log = AuditLogService.log_login(request, user=None, success=False)
        assert log.action == "login_failed"
        assert log.user is None
        assert log.response_status == 401
        assert log.ip_address == "192.168.1.100"

    def test_log_logout(self, user):
        """Test logging logout."""
        request = Mock(spec=["META", "method", "path"])
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        request.method = "POST"
        request.path = "/api/v1/auth/logout/"

        log = AuditLogService.log_logout(request, user)
        assert log.action == "logout"
        assert log.user == user

    def test_log_password_change(self, user):
        """Test logging password change."""
        request = Mock(spec=["META", "method", "path"])
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        request.method = "POST"
        request.path = "/api/v1/auth/password/change/"

        log = AuditLogService.log_password_change(request, user)
        assert log.action == "password_change"
        assert log.target_model == "User"
        assert log.target_id == str(user.id)

    def test_log_order_create(self, user, order):
        """Test logging order creation."""
        request = Mock(spec=["META", "method", "path"])
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        request.method = "POST"
        request.path = "/api/v1/orders/"

        log = AuditLogService.log_order_create(request, order, created_by=user)
        assert log.action == "order_create"
        assert log.target_model == "Order"
        assert log.target_id == str(order.id)
        assert log.restaurant == order.restaurant

    def test_log_order_cancel(self, user, order):
        """Test logging order cancellation."""
        request = Mock(spec=["META", "method", "path"])
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        request.method = "POST"
        request.path = f"/api/v1/orders/{order.id}/cancel/"

        log = AuditLogService.log_order_cancel(request, order, cancelled_by=user)
        assert log.action == "order_cancel"
        assert log.target_model == "Order"

    def test_log_settings_update(self, user, restaurant):
        """Test logging settings update."""
        request = Mock(spec=["META", "method", "path"])
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        request.method = "PATCH"
        request.path = "/api/v1/dashboard/settings/"

        changes = {"name": {"old": "Old Name", "new": "New Name"}}
        log = AuditLogService.log_settings_update(request, restaurant, changes, updated_by=user)
        assert log.action == "settings_update"
        assert log.changes == changes
        assert log.restaurant == restaurant

    def test_log_data_export(self, user, restaurant):
        """Test logging data export."""
        request = Mock(spec=["META", "method", "path"])
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        request.method = "GET"
        request.path = "/api/v1/dashboard/export/"

        log = AuditLogService.log_data_export(request, "orders", restaurant=restaurant, exported_by=user)
        assert log.action == "data_export"
        assert "orders" in log.description

    def test_log_action_convenience_function(self, user):
        """Test the convenience function."""
        log = log_action(
            action="login",
            user=user,
            description="Test login",
        )
        assert log.action == "login"
        assert log.user == user
