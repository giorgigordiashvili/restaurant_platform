"""
Audit logging middleware for sensitive operations.
"""

import logging

logger = logging.getLogger(__name__)


class AuditMiddleware:
    """
    Middleware to log sensitive operations to the audit log.

    Views can set audit attributes on the request:
        request._audit_action = 'user_create'
        request._audit_target_model = 'User'
        request._audit_target_id = str(user.id)
        request._audit_description = 'Created new user'
        request._audit_changes = {'email': user.email}
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Check if audit attributes are set
        if hasattr(request, "_audit_action"):
            self._create_audit_log(request, response)

        return response

    def _create_audit_log(self, request, response):
        """Create an audit log entry."""
        try:
            # Lazy import to avoid circular imports
            from apps.audit.models import AuditLog

            user = request.user if request.user.is_authenticated else None
            user_email = user.email if user else "anonymous"

            AuditLog.objects.create(
                user=user,
                user_email=user_email,
                ip_address=self._get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
                restaurant=getattr(request, "restaurant", None),
                action=request._audit_action,
                target_model=getattr(request, "_audit_target_model", ""),
                target_id=getattr(request, "_audit_target_id", ""),
                description=getattr(request, "_audit_description", ""),
                changes=getattr(request, "_audit_changes", {}),
                request_method=request.method,
                request_path=request.path[:500],
                response_status=response.status_code,
            )
        except Exception as e:
            # Don't let audit logging errors break the request
            logger.error(f"Failed to create audit log: {e}")

    def _get_client_ip(self, request):
        """Get the client's IP address from the request."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0].strip()
        else:
            ip = request.META.get("REMOTE_ADDR")
        return ip


def audit_action(action, target_model=None, target_id=None, description=None, changes=None):
    """
    Decorator to automatically add audit logging to a view.

    Usage:
        @audit_action('user_update', 'User')
        def update_user(request, user_id):
            ...
    """

    def decorator(view_func):
        from functools import wraps

        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            request._audit_action = action
            if target_model:
                request._audit_target_model = target_model
            if target_id:
                request._audit_target_id = str(target_id)
            if description:
                request._audit_description = description
            if changes:
                request._audit_changes = changes
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator
