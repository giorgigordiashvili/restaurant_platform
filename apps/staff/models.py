"""
Staff models for restaurant personnel management.
"""

import secrets
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class StaffRole(TimeStampedModel):
    """
    Defines roles and their permissions within a restaurant.
    Each restaurant can have custom roles with specific permissions.
    """

    # Default role permissions
    DEFAULT_PERMISSIONS = {
        "owner": {
            "menu": ["create", "read", "update", "delete"],
            "orders": ["create", "read", "update", "delete"],
            "tables": ["create", "read", "update", "delete"],
            "staff": ["create", "read", "update", "delete"],
            "settings": ["read", "update"],
            "analytics": ["read"],
            "reservations": ["create", "read", "update", "delete"],
        },
        "manager": {
            "menu": ["create", "read", "update", "delete"],
            "orders": ["create", "read", "update", "delete"],
            "tables": ["create", "read", "update", "delete"],
            "staff": ["read", "update"],
            "settings": ["read"],
            "analytics": ["read"],
            "reservations": ["create", "read", "update", "delete"],
        },
        "kitchen": {
            "menu": ["read"],
            "orders": ["read", "update"],
        },
        "bar": {
            "menu": ["read"],
            "orders": ["read", "update"],
        },
        "waiter": {
            "menu": ["read"],
            "orders": ["create", "read", "update"],
            "tables": ["read", "update"],
            "reservations": ["read"],
        },
    }

    ROLE_CHOICES = [
        ("owner", "Owner"),
        ("manager", "Manager"),
        ("kitchen", "Kitchen Staff"),
        ("bar", "Bar Staff"),
        ("waiter", "Waiter"),
        ("custom", "Custom Role"),
    ]

    name = models.CharField(max_length=50, choices=ROLE_CHOICES, default="waiter")
    display_name = models.CharField(max_length=100, blank=True)
    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="staff_roles",
    )
    permissions = models.JSONField(
        default=dict,
        help_text="JSON object mapping resources to allowed actions",
    )
    is_system_role = models.BooleanField(
        default=False,
        help_text="System roles cannot be deleted",
    )
    description = models.TextField(blank=True)

    class Meta:
        db_table = "staff_roles"
        unique_together = ["restaurant", "name"]
        ordering = ["name"]

    def __str__(self):
        return f"{self.get_display_name()} @ {self.restaurant.name}"

    def get_display_name(self):
        if self.display_name:
            return self.display_name
        return self.get_name_display()

    def save(self, *args, **kwargs):
        # Set default permissions based on role name
        if not self.permissions and self.name in self.DEFAULT_PERMISSIONS:
            self.permissions = self.DEFAULT_PERMISSIONS[self.name]
        super().save(*args, **kwargs)

    def has_permission(self, resource: str, action: str) -> bool:
        """Check if this role has a specific permission."""
        if resource not in self.permissions:
            return False
        allowed_actions = self.permissions[resource]
        return action in allowed_actions or "*" in allowed_actions

    @classmethod
    def create_default_roles(cls, restaurant):
        """Create default roles for a new restaurant."""
        roles = []
        for role_name in ["owner", "manager", "kitchen", "bar", "waiter"]:
            role, created = cls.objects.get_or_create(
                restaurant=restaurant,
                name=role_name,
                defaults={
                    "permissions": cls.DEFAULT_PERMISSIONS.get(role_name, {}),
                    "is_system_role": True,
                },
            )
            roles.append(role)
        return roles


class StaffMember(TimeStampedModel):
    """
    Links a user to a restaurant as a staff member with a specific role.
    A user can be staff at multiple restaurants.
    """

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="staff_memberships",
    )
    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="staff_members",
    )
    role = models.ForeignKey(
        StaffRole,
        on_delete=models.PROTECT,
        related_name="members",
    )
    is_active = models.BooleanField(default=True)
    permissions_override = models.JSONField(
        default=dict,
        blank=True,
        help_text="Individual permission overrides (additive)",
    )
    invited_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_invitations_sent",
    )
    invited_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, help_text="Internal notes about this staff member")

    class Meta:
        db_table = "staff_members"
        unique_together = ["user", "restaurant"]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email} - {self.role.get_display_name()} @ {self.restaurant.name}"

    def has_permission(self, resource: str, action: str) -> bool:
        """Check if this staff member has a specific permission."""
        # Check role permissions first
        if self.role.has_permission(resource, action):
            return True

        # Check individual overrides
        if resource in self.permissions_override:
            allowed = self.permissions_override[resource]
            return action in allowed or "*" in allowed

        return False

    def get_effective_permissions(self) -> dict:
        """Get combined permissions from role and overrides."""
        permissions = dict(self.role.permissions)

        # Merge individual overrides
        for resource, actions in self.permissions_override.items():
            if resource in permissions:
                # Combine unique actions
                permissions[resource] = list(set(permissions[resource] + actions))
            else:
                permissions[resource] = actions

        return permissions

    def deactivate(self):
        """Deactivate this staff membership."""
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])


class StaffInvitation(TimeStampedModel):
    """
    Email-based invitation for staff members.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("accepted", "Accepted"),
        ("expired", "Expired"),
        ("cancelled", "Cancelled"),
    ]

    restaurant = models.ForeignKey(
        "tenants.Restaurant",
        on_delete=models.CASCADE,
        related_name="staff_invitations",
    )
    email = models.EmailField()
    role = models.ForeignKey(
        StaffRole,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    invited_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="sent_invitations",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_invitations",
    )
    message = models.TextField(blank=True, help_text="Optional message to include in invitation")

    class Meta:
        db_table = "staff_invitations"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invitation for {self.email} to {self.restaurant.name}"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(48)
        if not self.expires_at:
            # Default expiry: 7 days
            self.expires_at = timezone.now() + timezone.timedelta(days=7)
        super().save(*args, **kwargs)

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    @property
    def is_valid(self) -> bool:
        return self.status == "pending" and not self.is_expired

    def accept(self, user) -> StaffMember:
        """Accept the invitation and create a staff membership."""
        if not self.is_valid:
            raise ValueError("This invitation is no longer valid")

        # Create or get staff member
        staff_member, created = StaffMember.objects.get_or_create(
            user=user,
            restaurant=self.restaurant,
            defaults={
                "role": self.role,
                "invited_by": self.invited_by,
                "invited_at": self.created_at,
                "joined_at": timezone.now(),
            },
        )

        if not created:
            # Update existing membership
            staff_member.role = self.role
            staff_member.is_active = True
            staff_member.joined_at = timezone.now()
            staff_member.save()

        # Mark invitation as accepted
        self.status = "accepted"
        self.accepted_at = timezone.now()
        self.accepted_by = user
        self.save()

        return staff_member

    def cancel(self):
        """Cancel this invitation."""
        self.status = "cancelled"
        self.save(update_fields=["status", "updated_at"])

    @classmethod
    def create_invitation(cls, restaurant, email, role, invited_by, message="", days_valid=7):
        """Create a new staff invitation."""
        # Cancel any existing pending invitations for this email
        cls.objects.filter(
            restaurant=restaurant,
            email=email.lower(),
            status="pending",
        ).update(status="cancelled")

        return cls.objects.create(
            restaurant=restaurant,
            email=email.lower(),
            role=role,
            invited_by=invited_by,
            message=message,
            expires_at=timezone.now() + timezone.timedelta(days=days_valid),
        )
