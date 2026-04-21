"""
DRF permission classes for the reviews app.
"""

from rest_framework.permissions import BasePermission

from apps.staff.models import StaffMember


class IsReviewOwner(BasePermission):
    """Allow edits / deletes only to the user who wrote the review."""

    def has_object_permission(self, request, view, obj):
        return obj.user_id == request.user.id


class IsStaffOfReviewedRestaurant(BasePermission):
    """
    Only active staff members of the review's restaurant can file a report.

    We intentionally require a live StaffMember row (not just a tenant
    middleware match) so a platform-level superuser isn't inadvertently
    counted as "the restaurant reported this".
    """

    def has_object_permission(self, request, view, obj):
        if not request.user.is_authenticated:
            return False
        return StaffMember.objects.filter(
            user=request.user,
            restaurant_id=obj.restaurant_id,
            is_active=True,
        ).exists()
