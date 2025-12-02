"""
Tests for audit models.
"""

import pytest

from apps.audit.models import AuditLog


@pytest.mark.django_db
class TestAuditLogModel:
    """Tests for AuditLog model."""

    def test_create_audit_log(self, user, restaurant):
        """Test creating an audit log."""
        log = AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="login",
            description="User logged in",
        )
        assert log.user == user
        assert log.user_email == user.email
        assert log.restaurant == restaurant
        assert log.action == "login"
        assert log.created_at is not None

    def test_audit_log_str(self, user):
        """Test audit log string representation."""
        log = AuditLog.objects.create(
            user=user,
            user_email=user.email,
            action="login",
            description="User logged in",
        )
        assert user.email in str(log)
        assert "login" in str(log)

    def test_audit_log_without_user(self, restaurant):
        """Test creating audit log without user (e.g., failed login)."""
        log = AuditLog.objects.create(
            user_email="unknown@example.com",
            restaurant=restaurant,
            action="login_failed",
            description="Failed login attempt",
            ip_address="192.168.1.1",
        )
        assert log.user is None
        assert log.user_email == "unknown@example.com"
        assert log.ip_address == "192.168.1.1"

    def test_audit_log_with_changes(self, user, restaurant):
        """Test audit log with changes dictionary."""
        changes = {
            "name": {"old": "Old Name", "new": "New Name"},
            "status": {"old": "pending", "new": "confirmed"},
        }
        log = AuditLog.objects.create(
            user=user,
            user_email=user.email,
            restaurant=restaurant,
            action="order_update",
            target_model="Order",
            target_id="12345",
            description="Order updated",
            changes=changes,
        )
        assert log.changes == changes
        assert log.target_model == "Order"
        assert log.target_id == "12345"

    def test_audit_log_with_request_metadata(self, user):
        """Test audit log with request metadata."""
        log = AuditLog.objects.create(
            user=user,
            user_email=user.email,
            action="settings_update",
            ip_address="10.0.0.1",
            user_agent="Mozilla/5.0",
            request_method="POST",
            request_path="/api/v1/dashboard/settings/",
            response_status=200,
        )
        assert log.ip_address == "10.0.0.1"
        assert log.user_agent == "Mozilla/5.0"
        assert log.request_method == "POST"
        assert log.request_path == "/api/v1/dashboard/settings/"
        assert log.response_status == 200

    def test_audit_log_ordering(self, user):
        """Test audit logs are ordered by created_at descending."""
        log1 = AuditLog.objects.create(
            user=user,
            user_email=user.email,
            action="login",
        )
        log2 = AuditLog.objects.create(
            user=user,
            user_email=user.email,
            action="logout",
        )
        logs = list(AuditLog.objects.all())
        assert logs[0] == log2  # Most recent first
        assert logs[1] == log1

    def test_action_choices(self):
        """Test all action choices are valid."""
        valid_actions = [choice[0] for choice in AuditLog.ACTION_CHOICES]
        assert "login" in valid_actions
        assert "logout" in valid_actions
        assert "password_change" in valid_actions
        assert "order_create" in valid_actions
        assert "payment_collect" in valid_actions
