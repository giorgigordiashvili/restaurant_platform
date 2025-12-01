"""
Staff serializers for API endpoints.
"""

from rest_framework import serializers

from apps.accounts.serializers import UserSerializer

from .models import StaffInvitation, StaffMember, StaffRole


class StaffRoleSerializer(serializers.ModelSerializer):
    """Serializer for staff roles."""

    class Meta:
        model = StaffRole
        fields = [
            "id",
            "name",
            "display_name",
            "permissions",
            "is_system_role",
            "description",
        ]
        read_only_fields = ["id", "is_system_role"]

    def get_display_name(self, obj):
        return obj.get_display_name()


class StaffRoleListSerializer(serializers.ModelSerializer):
    """Minimal serializer for role lists."""

    display_name = serializers.SerializerMethodField()

    class Meta:
        model = StaffRole
        fields = ["id", "name", "display_name"]

    def get_display_name(self, obj):
        return obj.get_display_name()


class StaffMemberSerializer(serializers.ModelSerializer):
    """Serializer for staff members list."""

    user = UserSerializer(read_only=True)
    role = StaffRoleListSerializer(read_only=True)
    role_id = serializers.PrimaryKeyRelatedField(
        queryset=StaffRole.objects.all(),
        source="role",
        write_only=True,
    )

    class Meta:
        model = StaffMember
        fields = [
            "id",
            "user",
            "role",
            "role_id",
            "is_active",
            "joined_at",
            "created_at",
        ]
        read_only_fields = ["id", "user", "joined_at", "created_at"]


class StaffMemberDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for staff member with permissions."""

    user = UserSerializer(read_only=True)
    role = StaffRoleSerializer(read_only=True)
    effective_permissions = serializers.SerializerMethodField()
    invited_by = UserSerializer(read_only=True)

    class Meta:
        model = StaffMember
        fields = [
            "id",
            "user",
            "role",
            "is_active",
            "permissions_override",
            "effective_permissions",
            "invited_by",
            "invited_at",
            "joined_at",
            "notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "invited_by",
            "invited_at",
            "joined_at",
            "created_at",
            "updated_at",
        ]

    def get_effective_permissions(self, obj):
        return obj.get_effective_permissions()


class StaffMemberUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating staff member."""

    role_id = serializers.PrimaryKeyRelatedField(
        queryset=StaffRole.objects.all(),
        source="role",
        required=False,
    )

    class Meta:
        model = StaffMember
        fields = [
            "role_id",
            "is_active",
            "permissions_override",
            "notes",
        ]

    def validate_role_id(self, value):
        """Ensure role belongs to the same restaurant."""
        staff = self.instance
        if value.restaurant_id != staff.restaurant_id:
            raise serializers.ValidationError("Role must belong to the same restaurant.")
        return value


class StaffInviteSerializer(serializers.Serializer):
    """Serializer for inviting new staff members."""

    email = serializers.EmailField()
    role_id = serializers.UUIDField()
    message = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_email(self, value):
        """Normalize email to lowercase."""
        return value.lower()

    def validate_role_id(self, value):
        """Ensure role exists and belongs to the restaurant."""
        restaurant = self.context.get("restaurant")
        try:
            role = StaffRole.objects.get(id=value, restaurant=restaurant)
            return role
        except StaffRole.DoesNotExist:
            raise serializers.ValidationError("Role not found.")

    def validate(self, data):
        """Check if user is already a staff member."""
        from apps.accounts.models import User

        restaurant = self.context.get("restaurant")
        email = data["email"]

        # Check if already a staff member
        try:
            user = User.objects.get(email__iexact=email)
            if StaffMember.objects.filter(user=user, restaurant=restaurant, is_active=True).exists():
                raise serializers.ValidationError({"email": "This user is already a staff member."})
        except User.DoesNotExist:
            pass  # New user will be invited

        # Check for pending invitation
        if StaffInvitation.objects.filter(
            restaurant=restaurant,
            email=email,
            status="pending",
        ).exists():
            raise serializers.ValidationError({"email": "An invitation is already pending for this email."})

        return data

    def create(self, validated_data):
        """Create staff invitation."""
        restaurant = self.context.get("restaurant")
        invited_by = self.context.get("request").user

        invitation = StaffInvitation.create_invitation(
            restaurant=restaurant,
            email=validated_data["email"],
            role=validated_data["role_id"],
            invited_by=invited_by,
            message=validated_data.get("message", ""),
        )
        return invitation


class StaffInvitationSerializer(serializers.ModelSerializer):
    """Serializer for staff invitations."""

    role = StaffRoleListSerializer(read_only=True)
    invited_by = UserSerializer(read_only=True)
    is_valid = serializers.SerializerMethodField()

    class Meta:
        model = StaffInvitation
        fields = [
            "id",
            "email",
            "role",
            "status",
            "invited_by",
            "message",
            "expires_at",
            "is_valid",
            "created_at",
        ]
        read_only_fields = fields

    def get_is_valid(self, obj):
        return obj.is_valid


class AcceptInvitationSerializer(serializers.Serializer):
    """Serializer for accepting staff invitation."""

    token = serializers.CharField()

    def validate_token(self, value):
        """Validate the invitation token."""
        try:
            invitation = StaffInvitation.objects.get(token=value)
            if not invitation.is_valid:
                raise serializers.ValidationError("This invitation is no longer valid.")
            return invitation
        except StaffInvitation.DoesNotExist:
            raise serializers.ValidationError("Invalid invitation token.")
