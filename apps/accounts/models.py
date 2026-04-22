"""
User models for the restaurant platform.
"""

import secrets
import string
import uuid
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel
from apps.core.utils.storage import user_avatar_path
from apps.core.utils.validators import phone_validator

from .managers import UserManager

REFERRAL_CODE_LENGTH = 8
REFERRAL_CODE_ALPHABET = string.ascii_uppercase + string.digits


class User(AbstractUser):
    """
    Custom user model with email as the primary identifier.
    """

    LANGUAGE_CHOICES = [
        ("ka", "Georgian"),
        ("en", "English"),
        ("ru", "Russian"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Override username to make it optional
    username = models.CharField(max_length=150, unique=True, null=True, blank=True)

    # Email as primary identifier
    email = models.EmailField(
        unique=True,
        db_index=True,
        error_messages={
            "unique": "A user with this email already exists.",
        },
    )

    # Phone number
    phone_number = models.CharField(max_length=20, blank=True, null=True, validators=[phone_validator], db_index=True)
    phone_verified = models.BooleanField(default=False)

    # Profile
    avatar = models.ImageField(upload_to=user_avatar_path, blank=True, null=True)
    preferred_language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default="ka")

    # Security
    last_login_ip = models.GenericIPAddressField(blank=True, null=True)
    failed_login_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(blank=True, null=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []  # Email is already required

    class Meta:
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["phone_number"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        # Auto-generate username from email if not provided
        if not self.username:
            self.username = self.email.split("@")[0]
            # Ensure uniqueness
            base_username = self.username
            counter = 1
            while User.objects.filter(username=self.username).exclude(pk=self.pk).exists():
                self.username = f"{base_username}{counter}"
                counter += 1
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        """Return the user's full name."""
        name = f"{self.first_name} {self.last_name}".strip()
        return name if name else self.email

    def is_account_locked(self):
        """Check if the account is currently locked."""
        if not self.locked_until:
            return False
        from django.utils import timezone

        return self.locked_until > timezone.now()

    def increment_failed_login(self):
        """Increment failed login counter and lock if necessary."""
        self.failed_login_attempts += 1

        # Lock account after 5 failed attempts for 30 minutes
        if self.failed_login_attempts >= 5:
            from datetime import timedelta

            from django.utils import timezone

            self.locked_until = timezone.now() + timedelta(minutes=30)

        self.save(update_fields=["failed_login_attempts", "locked_until", "updated_at"])

    def reset_failed_login(self):
        """Reset failed login counter after successful login."""
        self.failed_login_attempts = 0
        self.locked_until = None
        self.save(update_fields=["failed_login_attempts", "locked_until", "updated_at"])

    def update_last_login_ip(self, ip_address):
        """Update the last login IP address."""
        self.last_login_ip = ip_address
        self.save(update_fields=["last_login_ip", "updated_at"])


class UserProfile(TimeStampedModel):
    """
    Extended user profile for additional information.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")

    date_of_birth = models.DateField(blank=True, null=True)

    # Preferences stored as JSON
    preferences = models.JSONField(
        default=dict, blank=True, help_text="User preferences (notifications, dietary, etc.)"
    )

    # Loyalty
    loyalty_points = models.PositiveIntegerField(default=0)
    total_orders = models.PositiveIntegerField(default=0)
    total_spent = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Marketing
    email_notifications = models.BooleanField(default=True)
    sms_notifications = models.BooleanField(default=False)
    push_notifications = models.BooleanField(default=True)

    # Referral + wallet
    referral_code = models.CharField(
        max_length=REFERRAL_CODE_LENGTH,
        unique=True,
        blank=True,
        default="",
        db_index=True,
        help_text="Auto-generated code the user shares to refer others.",
    )
    referred_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referrals_made",
        help_text="The user whose referral_code was claimed at signup. Set once.",
    )
    wallet_balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Spendable balance in GEL. Source-of-truth is the WalletTransaction ledger.",
    )
    referral_percent_override = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text=(
            "Superuser-only. When set, overrides settings.REFERRAL_DEFAULT_PERCENT for "
            "orders placed by users this profile has referred."
        ),
    )

    class Meta:
        db_table = "user_profiles"
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

    def __str__(self):
        return f"Profile of {self.user.email}"

    def save(self, *args, **kwargs):
        if not self.referral_code:
            self.referral_code = self._mint_referral_code()
        super().save(*args, **kwargs)

    @classmethod
    def _mint_referral_code(cls) -> str:
        """Generate a unique 8-char alphanumeric code, retrying on collision."""
        for _ in range(10):
            candidate = "".join(secrets.choice(REFERRAL_CODE_ALPHABET) for _ in range(REFERRAL_CODE_LENGTH))
            if not cls.objects.filter(referral_code=candidate).exists():
                return candidate
        # Astronomically unlikely; fall through and let the unique constraint
        # raise IntegrityError so the caller knows we ran out of luck.
        return "".join(secrets.choice(REFERRAL_CODE_ALPHABET) for _ in range(REFERRAL_CODE_LENGTH))

    def add_loyalty_points(self, points):
        """Add loyalty points to user."""
        self.loyalty_points += points
        self.save(update_fields=["loyalty_points", "updated_at"])

    def increment_order_stats(self, amount):
        """Update order statistics."""
        self.total_orders += 1
        self.total_spent += amount
        self.save(update_fields=["total_orders", "total_spent", "updated_at"])
