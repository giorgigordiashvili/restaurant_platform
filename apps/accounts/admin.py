"""
Admin configuration for the accounts app.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from unfold.admin import ModelAdmin as UnfoldModelAdmin

from .models import User, UserProfile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    fk_name = "user"
    can_delete = False
    verbose_name_plural = "Profile"
    extra = 0
    max_num = 1


@admin.register(User)
class UserAdmin(UnfoldModelAdmin, BaseUserAdmin):
    """Custom admin for User model."""

    inlines = [UserProfileInline]

    def get_inlines(self, request, obj):
        """Only show profile inline when editing existing user (signal creates it)."""
        if obj is None:
            return []
        return self.inlines

    list_display = [
        "email",
        "first_name",
        "last_name",
        "phone_number",
        "is_active",
        "is_staff",
        "created_at",
    ]

    list_filter = [
        "is_active",
        "is_staff",
        "is_superuser",
        "preferred_language",
        "created_at",
    ]

    search_fields = [
        "email",
        "first_name",
        "last_name",
        "phone_number",
    ]

    ordering = ["-created_at"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Personal Info",
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "phone_number",
                    "phone_verified",
                    "avatar",
                    "preferred_language",
                )
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Security",
            {
                "fields": (
                    "last_login_ip",
                    "failed_login_attempts",
                    "locked_until",
                ),
                "classes": ("collapse",),
            },
        ),
        ("Important dates", {"fields": ("last_login", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    readonly_fields = ["created_at", "updated_at", "last_login"]

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "password1",
                    "password2",
                    "first_name",
                    "last_name",
                    "phone_number",
                    "preferred_language",
                    "is_active",
                    "is_staff",
                ),
            },
        ),
    )


@admin.register(UserProfile)
class UserProfileAdmin(UnfoldModelAdmin):
    """Admin for UserProfile model."""

    list_display = [
        "user",
        "loyalty_points",
        "total_orders",
        "total_spent",
        "wallet_balance",
        "referral_code",
        "email_notifications",
        "created_at",
    ]

    list_filter = [
        "email_notifications",
        "sms_notifications",
        "push_notifications",
        "created_at",
    ]

    search_fields = [
        "user__email",
        "user__first_name",
        "user__last_name",
        "referral_code",
    ]

    readonly_fields = [
        "created_at",
        "updated_at",
        "referral_code",
        "referred_by",
        "wallet_balance",
    ]

    def get_fieldsets(self, request, obj=None):
        """Show referral / wallet fields only to superusers."""
        base = [
            (None, {"fields": ("user",)}),
            (
                "Profile",
                {
                    "fields": (
                        "date_of_birth",
                        "preferences",
                    )
                },
            ),
            (
                "Loyalty",
                {
                    "fields": (
                        "loyalty_points",
                        "total_orders",
                        "total_spent",
                    )
                },
            ),
            (
                "Notifications",
                {
                    "fields": (
                        "email_notifications",
                        "sms_notifications",
                        "push_notifications",
                    )
                },
            ),
        ]
        if request.user.is_superuser:
            base.append(
                (
                    "Referral & Wallet (superuser)",
                    {
                        "fields": (
                            "referral_code",
                            "referred_by",
                            "wallet_balance",
                            "referral_percent_override",
                        ),
                    },
                )
            )
        base.append(("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}))
        return base
