"""
Serializers for the accounts app.
"""

from django.contrib.auth.password_validation import validate_password

from rest_framework import serializers

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import User, UserProfile


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for user profile."""

    class Meta:
        model = UserProfile
        fields = [
            "date_of_birth",
            "preferences",
            "loyalty_points",
            "total_orders",
            "total_spent",
            "email_notifications",
            "sms_notifications",
            "push_notifications",
        ]
        read_only_fields = ["loyalty_points", "total_orders", "total_spent"]


class UserSerializer(serializers.ModelSerializer):
    """Serializer for user details."""

    profile = UserProfileSerializer(read_only=True)
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone_number",
            "phone_verified",
            "avatar",
            "preferred_language",
            "created_at",
            "profile",
        ]
        read_only_fields = ["id", "email", "phone_verified", "created_at"]


class UserRegistrationSerializer(serializers.ModelSerializer):
    """Serializer for user registration."""

    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password], style={"input_type": "password"}
    )
    password_confirm = serializers.CharField(write_only=True, required=True, style={"input_type": "password"})
    referral_code = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=8,
        help_text="Optional code from an existing customer. Sets the new user's referrer.",
    )

    class Meta:
        model = User
        fields = [
            "email",
            "password",
            "password_confirm",
            "first_name",
            "last_name",
            "phone_number",
            "preferred_language",
            "referral_code",
        ]

    def validate_email(self, value):
        """Validate email is unique (case-insensitive)."""
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value.lower()

    def validate_referral_code(self, value):
        """Resolve the referral code to a UserProfile up-front so we can 400 cleanly."""
        if not value:
            return ""
        normalized = value.strip().upper()
        if not UserProfile.objects.filter(referral_code=normalized).exists():
            raise serializers.ValidationError("Unknown referral code.")
        return normalized

    def validate(self, attrs):
        """Validate passwords match."""
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        return attrs

    def create(self, validated_data):
        """Create the user and (if a referral code was supplied) tie the profile to a referrer."""
        validated_data.pop("password_confirm")
        password = validated_data.pop("password")
        referral_code = validated_data.pop("referral_code", "") or ""

        user = User.objects.create_user(password=password, **validated_data)

        if referral_code:
            referrer_profile = UserProfile.objects.filter(referral_code=referral_code).first()
            if referrer_profile and referrer_profile.user_id != user.id:
                # The post_save signal on User creates the empty profile; pull it
                # back out and attach the referrer. Using update() avoids racing
                # the signal-created instance through .save() a second time.
                UserProfile.objects.filter(user=user).update(referred_by_id=referrer_profile.user_id)

        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating user profile."""

    profile = UserProfileSerializer(required=False)

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "phone_number",
            "avatar",
            "preferred_language",
            "profile",
        ]

    def update(self, instance, validated_data):
        """Update user and profile."""
        profile_data = validated_data.pop("profile", None)

        # Update user fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Update profile fields
        if profile_data and hasattr(instance, "profile"):
            for attr, value in profile_data.items():
                setattr(instance.profile, attr, value)
            instance.profile.save()

        return instance


class ChangePasswordSerializer(serializers.Serializer):
    """Serializer for password change."""

    old_password = serializers.CharField(required=True, style={"input_type": "password"})
    new_password = serializers.CharField(
        required=True, validators=[validate_password], style={"input_type": "password"}
    )
    new_password_confirm = serializers.CharField(required=True, style={"input_type": "password"})

    def validate_old_password(self, value):
        """Check that old password is correct."""
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate(self, attrs):
        """Validate new passwords match."""
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({"new_password_confirm": "New passwords do not match."})
        return attrs

    def save(self):
        """Save the new password."""
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save()
        return user


class PasswordResetRequestSerializer(serializers.Serializer):
    """Serializer for requesting password reset."""

    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        """Normalize email."""
        return value.lower()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """Serializer for confirming password reset."""

    token = serializers.CharField(required=True)
    new_password = serializers.CharField(
        required=True, validators=[validate_password], style={"input_type": "password"}
    )
    new_password_confirm = serializers.CharField(required=True, style={"input_type": "password"})

    def validate(self, attrs):
        """Validate passwords match."""
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({"new_password_confirm": "Passwords do not match."})
        return attrs


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Custom token serializer that adds user data to response
    and handles account locking.
    """

    def validate(self, attrs):
        email = attrs.get("email", "").lower()

        try:
            user = User.objects.get(email__iexact=email)

            # Check if account is locked
            if user.is_account_locked():
                raise serializers.ValidationError({"detail": "Account is temporarily locked. Please try again later."})

        except User.DoesNotExist:
            pass

        try:
            data = super().validate(attrs)

            # Add user info to response
            data["user"] = {
                "id": str(self.user.id),
                "email": self.user.email,
                "first_name": self.user.first_name,
                "last_name": self.user.last_name,
                "preferred_language": self.user.preferred_language,
            }

            # Reset failed login attempts on success
            self.user.reset_failed_login()

            return data

        except Exception:
            # Increment failed login attempts
            try:
                user = User.objects.get(email__iexact=email)
                user.increment_failed_login()
            except User.DoesNotExist:
                pass
            raise

    @classmethod
    def get_token(cls, user):
        """Add custom claims to token."""
        token = super().get_token(user)

        # Add custom claims
        token["email"] = user.email
        token["preferred_language"] = user.preferred_language

        return token


# ── Social auth (allauth + dj-rest-auth) ─────────────────────────────
#
# Everything below is loaded lazily: the imports reach into dj-rest-auth
# which pulls allauth in, so importing at module top level would fail in any
# environment that hasn't installed the packages. Module-load stays cheap.


class SocialJWTSerializer(serializers.Serializer):
    """
    Shape dj-rest-auth's JWT response so it matches ``CustomTokenObtainPairSerializer``'s
    response verbatim. The frontend's AuthContext consumes
    ``{access, refresh, user}`` and we don't want it to branch on whether the
    login came from password or from Google/Facebook.
    """

    access = serializers.CharField()
    refresh = serializers.CharField()
    user = serializers.SerializerMethodField()

    def get_user(self, instance):
        user = instance.get("user")
        if not user:
            return None
        return {
            "id": str(user.id),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "preferred_language": user.preferred_language,
        }


def _build_social_login_serializer():
    """
    Factory for the provider-agnostic social-login input serializer. dj-rest-auth's
    ``SocialLoginSerializer`` already validates the posted ``access_token`` /
    ``id_token`` against the configured provider adapter — we only add our
    optional ``referral_code`` field so the adapter can read it back off the
    request at save-user time.
    """
    from dj_rest_auth.registration.serializers import SocialLoginSerializer as BaseSocialLoginSerializer

    class SocialLoginSerializer(BaseSocialLoginSerializer):
        referral_code = serializers.CharField(required=False, allow_blank=True, max_length=8)

        def validate_referral_code(self, value):
            if not value:
                return ""
            return value.strip().upper()

    return SocialLoginSerializer


SocialLoginSerializer = _build_social_login_serializer()
