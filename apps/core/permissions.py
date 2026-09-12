"""
Custom permission classes for the restaurant platform.
"""

from rest_framework.permissions import BasePermission


class IsOwnerOrReadOnly(BasePermission):
    """
    Object-level permission to only allow owners of an object to edit it.
    """

    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed for any request
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True

        # Write permissions are only allowed to the owner
        return hasattr(obj, "user") and obj.user == request.user


class IsTenantOwner(BasePermission):
    """
    Permission check if user is the restaurant owner.
    """

    message = "You must be the restaurant owner to perform this action."

    def has_permission(self, request, view):
        restaurant = getattr(request, "restaurant", None)
        if not restaurant:
            return False
        return restaurant.owner == request.user


class IsTenantStaff(BasePermission):
    """
    Permission check if user is staff at the current restaurant.
    """

    message = "You must be a staff member of this restaurant."

    def has_permission(self, request, view):
        restaurant = getattr(request, "restaurant", None)
        if not restaurant:
            return False

        if not request.user.is_authenticated:
            return False

        # Check if user is the owner
        if restaurant.owner == request.user:
            return True

        # Check if user is staff
        return request.user.staff_memberships.filter(restaurant=restaurant, is_active=True).exists()


class IsTenantManager(BasePermission):
    """
    Permission check if user is a manager at the current restaurant.
    """

    message = "You must be a manager of this restaurant."

    def has_permission(self, request, view):
        restaurant = getattr(request, "restaurant", None)
        if not restaurant:
            return False

        if not request.user.is_authenticated:
            return False

        # Owner has all permissions
        if restaurant.owner == request.user:
            return True

        # Check for manager role
        return request.user.staff_memberships.filter(
            restaurant=restaurant, is_active=True, role__name__in=["owner", "manager"]
        ).exists()


class HasStaffPermission(BasePermission):
    """
    Check if user has specific staff permission for a resource and action.

    Usage:
        permission_classes = [HasStaffPermission]
        required_permission = ('menu', 'create')  # (resource, action)
    """

    message = "You don't have permission to perform this action."

    def has_permission(self, request, view):
        restaurant = getattr(request, "restaurant", None)
        if not restaurant:
            return False

        if not request.user.is_authenticated:
            return False

        # Owner has all permissions
        if restaurant.owner == request.user:
            return True

        # Get required permission from view
        required = getattr(view, "required_permission", None)
        if not required:
            return True  # No specific permission required

        resource, action = required

        return _member_can(request.user, restaurant, resource, action)


def _member_can(user, restaurant, resource, action):
    """Effective (role + per-member override) permission check for an active staff member."""
    try:
        staff = user.staff_memberships.get(restaurant=restaurant, is_active=True)
    except Exception:
        return False
    allowed = staff.get_effective_permissions().get(resource, [])
    return action in allowed or "*" in allowed


class IsRestaurantActive(BasePermission):
    """
    Permission check if the restaurant is active.
    """

    message = "This restaurant is not currently active."

    def has_permission(self, request, view):
        restaurant = getattr(request, "restaurant", None)
        if restaurant:
            return restaurant.is_active
        return True  # Allow if no restaurant context


class AllowAny(BasePermission):
    """
    Allow any access (for public endpoints).
    """

    def has_permission(self, request, view):
        return True


def staff_can(request, resource, action):
    """
    Same rule as HasStaffPermission, callable outside a view: the owner may do
    anything; otherwise the active StaffMember's role must grant the action.
    """
    restaurant = getattr(request, "restaurant", None)
    user = getattr(request, "user", None)
    if not restaurant or user is None or not user.is_authenticated:
        return False
    if restaurant.owner_id == user.pk:
        return True
    return _member_can(user, restaurant, resource, action)


def ModuleRequired(code):
    """
    Permission class factory: the request's restaurant must have the module
    switched on (see apps.core.modules). Renders as 403 {"code": "module_disabled"}.
    """
    from rest_framework.exceptions import PermissionDenied

    class _ModuleRequired(BasePermission):
        message = f"The {code} module is switched off at this restaurant."

        def has_permission(self, request, view):
            from apps.core.modules import is_enabled

            restaurant = getattr(request, "restaurant", None)
            if restaurant is None:
                return True  # tenant resolution failures are reported by the other classes
            if is_enabled(restaurant, code):
                return True
            raise PermissionDenied({"code": "module_disabled", "module": code, "message": self.message})

    _ModuleRequired.__name__ = f"ModuleRequired_{code}"
    return _ModuleRequired
