"""
Tests for staff models.
"""

import pytest
from django.utils import timezone

from apps.staff.models import StaffInvitation, StaffMember, StaffRole


@pytest.mark.django_db
class TestStaffRoleModel:
    """Tests for StaffRole model."""

    def test_create_role(self, restaurant):
        """Test creating a staff role."""
        role = StaffRole.objects.create(
            restaurant=restaurant,
            name="waiter",
        )
        assert role.name == "waiter"
        assert role.restaurant == restaurant

    def test_role_str(self, restaurant):
        """Test role string representation."""
        role = StaffRole.objects.create(
            restaurant=restaurant,
            name="manager",
        )
        assert restaurant.name in str(role)
        assert "Manager" in str(role)

    def test_default_permissions(self, restaurant):
        """Test that default permissions are set based on role name."""
        role = StaffRole.objects.create(
            restaurant=restaurant,
            name="waiter",
        )
        assert "menu" in role.permissions
        assert "read" in role.permissions["menu"]

    def test_create_default_roles(self, restaurant):
        """Test creating default roles for a restaurant."""
        roles = StaffRole.create_default_roles(restaurant)
        assert len(roles) == 5
        role_names = [r.name for r in roles]
        assert "owner" in role_names
        assert "manager" in role_names
        assert "kitchen" in role_names
        assert "bar" in role_names
        assert "waiter" in role_names

    def test_has_permission(self, restaurant):
        """Test permission checking."""
        role = StaffRole.objects.create(
            restaurant=restaurant,
            name="waiter",
        )
        assert role.has_permission("menu", "read") is True
        assert role.has_permission("menu", "delete") is False
        assert role.has_permission("settings", "update") is False


@pytest.mark.django_db
class TestStaffMemberModel:
    """Tests for StaffMember model."""

    def test_create_staff_member(self, user, restaurant, staff_roles):
        """Test creating a staff member."""
        waiter_role = next(r for r in staff_roles if r.name == "waiter")
        member = StaffMember.objects.create(
            user=user,
            restaurant=restaurant,
            role=waiter_role,
        )
        assert member.user == user
        assert member.restaurant == restaurant
        assert member.role == waiter_role
        assert member.is_active is True

    def test_staff_member_str(self, waiter_staff):
        """Test staff member string representation."""
        assert waiter_staff.user.email in str(waiter_staff)
        assert waiter_staff.restaurant.name in str(waiter_staff)

    def test_has_permission(self, waiter_staff):
        """Test staff member permission checking."""
        assert waiter_staff.has_permission("menu", "read") is True
        assert waiter_staff.has_permission("menu", "delete") is False

    def test_permissions_override(self, waiter_staff):
        """Test individual permission overrides."""
        waiter_staff.permissions_override = {"menu": ["delete"]}
        waiter_staff.save()
        assert waiter_staff.has_permission("menu", "delete") is True

    def test_get_effective_permissions(self, waiter_staff):
        """Test getting combined permissions."""
        waiter_staff.permissions_override = {"analytics": ["read"]}
        waiter_staff.save()

        perms = waiter_staff.get_effective_permissions()
        assert "menu" in perms
        assert "analytics" in perms

    def test_deactivate(self, waiter_staff):
        """Test deactivating a staff member."""
        assert waiter_staff.is_active is True
        waiter_staff.deactivate()
        assert waiter_staff.is_active is False


@pytest.mark.django_db
class TestStaffInvitationModel:
    """Tests for StaffInvitation model."""

    def test_create_invitation(self, restaurant, staff_roles, user):
        """Test creating a staff invitation."""
        waiter_role = next(r for r in staff_roles if r.name == "waiter")
        invitation = StaffInvitation.create_invitation(
            restaurant=restaurant,
            email="newstaff@example.com",
            role=waiter_role,
            invited_by=user,
        )
        assert invitation.email == "newstaff@example.com"
        assert invitation.role == waiter_role
        assert invitation.status == "pending"
        assert invitation.token is not None

    def test_invitation_str(self, restaurant, staff_roles, user):
        """Test invitation string representation."""
        waiter_role = next(r for r in staff_roles if r.name == "waiter")
        invitation = StaffInvitation.create_invitation(
            restaurant=restaurant,
            email="newstaff@example.com",
            role=waiter_role,
            invited_by=user,
        )
        assert "newstaff@example.com" in str(invitation)
        assert restaurant.name in str(invitation)

    def test_is_valid(self, restaurant, staff_roles, user):
        """Test invitation validity check."""
        waiter_role = next(r for r in staff_roles if r.name == "waiter")
        invitation = StaffInvitation.create_invitation(
            restaurant=restaurant,
            email="newstaff@example.com",
            role=waiter_role,
            invited_by=user,
        )
        assert invitation.is_valid is True

    def test_is_expired(self, restaurant, staff_roles, user):
        """Test invitation expiry check."""
        waiter_role = next(r for r in staff_roles if r.name == "waiter")
        invitation = StaffInvitation.create_invitation(
            restaurant=restaurant,
            email="newstaff@example.com",
            role=waiter_role,
            invited_by=user,
        )
        # Set expiry to past
        invitation.expires_at = timezone.now() - timezone.timedelta(days=1)
        invitation.save()
        assert invitation.is_expired is True
        assert invitation.is_valid is False

    def test_accept_invitation(self, restaurant, staff_roles, user, another_user):
        """Test accepting an invitation."""
        waiter_role = next(r for r in staff_roles if r.name == "waiter")
        invitation = StaffInvitation.create_invitation(
            restaurant=restaurant,
            email=another_user.email,
            role=waiter_role,
            invited_by=user,
        )
        staff_member = invitation.accept(another_user)

        assert staff_member.user == another_user
        assert staff_member.restaurant == restaurant
        assert staff_member.role == waiter_role
        assert invitation.status == "accepted"

    def test_cancel_invitation(self, restaurant, staff_roles, user):
        """Test cancelling an invitation."""
        waiter_role = next(r for r in staff_roles if r.name == "waiter")
        invitation = StaffInvitation.create_invitation(
            restaurant=restaurant,
            email="newstaff@example.com",
            role=waiter_role,
            invited_by=user,
        )
        invitation.cancel()
        assert invitation.status == "cancelled"
        assert invitation.is_valid is False
