"""
Audit logging service for tracking sensitive operations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from django.http import HttpRequest

if TYPE_CHECKING:
    from .models import AuditLog

logger = logging.getLogger(__name__)


class AuditLogService:
    """Service for creating audit log entries."""

    @classmethod
    def log(
        cls,
        action: str,
        request: Optional[HttpRequest] = None,
        user=None,
        restaurant=None,
        target_model: str = "",
        target_id: str = "",
        description: str = "",
        changes: Optional[dict] = None,
        response_status: Optional[int] = None,
    ) -> "AuditLog":
        """
        Create an audit log entry.

        Args:
            action: The action being logged (from ACTION_CHOICES)
            request: The HTTP request (optional, for extracting metadata)
            user: The user performing the action
            restaurant: The restaurant context (if applicable)
            target_model: The model being affected (e.g., 'Order', 'User')
            target_id: The ID of the affected object
            description: Human-readable description of the action
            changes: Dictionary of changes made (for updates)
            response_status: HTTP response status code

        Returns:
            The created AuditLog instance
        """
        from .models import AuditLog

        # Extract request metadata
        ip_address = None
        user_agent = ""
        request_method = ""
        request_path = ""

        if request:
            ip_address = cls._get_client_ip(request)
            user_agent = request.META.get("HTTP_USER_AGENT", "")[:500]
            request_method = request.method or ""
            request_path = request.path[:500] if request.path else ""

            # Get user from request if not provided
            if user is None and hasattr(request, "user") and request.user.is_authenticated:
                user = request.user

            # Get restaurant from request if not provided
            if restaurant is None and hasattr(request, "restaurant"):
                restaurant = request.restaurant

        # Get user email
        user_email = ""
        if user:
            user_email = getattr(user, "email", "")

        try:
            audit_log = AuditLog.objects.create(
                user=user,
                user_email=user_email,
                ip_address=ip_address,
                user_agent=user_agent,
                restaurant=restaurant,
                action=action,
                target_model=target_model,
                target_id=str(target_id) if target_id else "",
                description=description,
                changes=changes or {},
                request_method=request_method,
                request_path=request_path,
                response_status=response_status,
            )
            return audit_log
        except Exception as e:
            logger.error(f"Failed to create audit log: {e}")
            raise

    @classmethod
    def _get_client_ip(cls, request: HttpRequest) -> Optional[str]:
        """Extract client IP address from request."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0].strip()
        else:
            ip = request.META.get("REMOTE_ADDR")
        return ip

    @classmethod
    def log_login(cls, request: HttpRequest, user, success: bool = True) -> "AuditLog":
        """Log a login attempt."""
        action = "login" if success else "login_failed"
        description = f"User {'logged in successfully' if success else 'failed to log in'}"
        return cls.log(
            action=action,
            request=request,
            user=user if success else None,
            description=description,
            response_status=200 if success else 401,
        )

    @classmethod
    def log_logout(cls, request: HttpRequest, user) -> "AuditLog":
        """Log a logout."""
        return cls.log(
            action="logout",
            request=request,
            user=user,
            description="User logged out",
        )

    @classmethod
    def log_password_change(cls, request: HttpRequest, user) -> "AuditLog":
        """Log a password change."""
        return cls.log(
            action="password_change",
            request=request,
            user=user,
            target_model="User",
            target_id=str(user.id),
            description="User changed their password",
        )

    @classmethod
    def log_password_reset(cls, request: HttpRequest, user) -> "AuditLog":
        """Log a password reset."""
        return cls.log(
            action="password_reset",
            request=request,
            user=user,
            target_model="User",
            target_id=str(user.id),
            description="User reset their password",
        )

    @classmethod
    def log_user_create(cls, request: HttpRequest, created_user, created_by=None) -> "AuditLog":
        """Log user creation."""
        return cls.log(
            action="user_create",
            request=request,
            user=created_by,
            target_model="User",
            target_id=str(created_user.id),
            description=f"User {created_user.email} was created",
        )

    @classmethod
    def log_user_update(cls, request: HttpRequest, updated_user, changes: dict, updated_by=None) -> "AuditLog":
        """Log user update."""
        return cls.log(
            action="user_update",
            request=request,
            user=updated_by,
            target_model="User",
            target_id=str(updated_user.id),
            description=f"User {updated_user.email} was updated",
            changes=changes,
        )

    @classmethod
    def log_user_delete(cls, request: HttpRequest, deleted_user, deleted_by=None) -> "AuditLog":
        """Log user deletion."""
        return cls.log(
            action="user_delete",
            request=request,
            user=deleted_by,
            target_model="User",
            target_id=str(deleted_user.id),
            description=f"User {deleted_user.email} was deleted",
        )

    @classmethod
    def log_staff_add(cls, request: HttpRequest, staff_member, restaurant, added_by=None) -> "AuditLog":
        """Log staff member addition."""
        return cls.log(
            action="staff_add",
            request=request,
            user=added_by,
            restaurant=restaurant,
            target_model="StaffMember",
            target_id=str(staff_member.id),
            description=f"Staff member {staff_member.user.email} was added",
        )

    @classmethod
    def log_staff_remove(cls, request: HttpRequest, staff_member, restaurant, removed_by=None) -> "AuditLog":
        """Log staff member removal."""
        return cls.log(
            action="staff_remove",
            request=request,
            user=removed_by,
            restaurant=restaurant,
            target_model="StaffMember",
            target_id=str(staff_member.id),
            description=f"Staff member {staff_member.user.email} was removed",
        )

    @classmethod
    def log_order_create(cls, request: HttpRequest, order, created_by=None) -> "AuditLog":
        """Log order creation."""
        return cls.log(
            action="order_create",
            request=request,
            user=created_by,
            restaurant=order.restaurant,
            target_model="Order",
            target_id=str(order.id),
            description=f"Order #{order.order_number} was created",
        )

    @classmethod
    def log_order_update(cls, request: HttpRequest, order, changes: dict, updated_by=None) -> "AuditLog":
        """Log order update."""
        return cls.log(
            action="order_update",
            request=request,
            user=updated_by,
            restaurant=order.restaurant,
            target_model="Order",
            target_id=str(order.id),
            description=f"Order #{order.order_number} was updated",
            changes=changes,
        )

    @classmethod
    def log_order_cancel(cls, request: HttpRequest, order, cancelled_by=None) -> "AuditLog":
        """Log order cancellation."""
        return cls.log(
            action="order_cancel",
            request=request,
            user=cancelled_by,
            restaurant=order.restaurant,
            target_model="Order",
            target_id=str(order.id),
            description=f"Order #{order.order_number} was cancelled",
        )

    @classmethod
    def log_payment_collect(cls, request: HttpRequest, payment, collected_by=None) -> "AuditLog":
        """Log payment collection."""
        return cls.log(
            action="payment_collect",
            request=request,
            user=collected_by,
            restaurant=payment.order.restaurant,
            target_model="Payment",
            target_id=str(payment.id),
            description=f"Payment of {payment.total_amount} was collected",
        )

    @classmethod
    def log_settings_update(cls, request: HttpRequest, restaurant, changes: dict, updated_by=None) -> "AuditLog":
        """Log settings update."""
        return cls.log(
            action="settings_update",
            request=request,
            user=updated_by,
            restaurant=restaurant,
            target_model="Restaurant",
            target_id=str(restaurant.id),
            description="Restaurant settings were updated",
            changes=changes,
        )

    @classmethod
    def log_data_export(cls, request: HttpRequest, export_type: str, restaurant=None, exported_by=None) -> "AuditLog":
        """Log data export."""
        return cls.log(
            action="data_export",
            request=request,
            user=exported_by,
            restaurant=restaurant,
            description=f"Data export: {export_type}",
        )


# Convenience function for quick logging
def log_action(
    action: str,
    request: Optional[HttpRequest] = None,
    **kwargs,
) -> "AuditLog":
    """Convenience function to log an action."""
    return AuditLogService.log(action=action, request=request, **kwargs)
