"""
Admin configuration for the accounts app.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User, UserProfile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Profile'


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Custom admin for User model."""

    inlines = [UserProfileInline]

    list_display = [
        'email',
        'first_name',
        'last_name',
        'phone_number',
        'is_active',
        'is_staff',
        'created_at',
    ]

    list_filter = [
        'is_active',
        'is_staff',
        'is_superuser',
        'preferred_language',
        'created_at',
    ]

    search_fields = [
        'email',
        'first_name',
        'last_name',
        'phone_number',
    ]

    ordering = ['-created_at']

    fieldsets = (
        (None, {
            'fields': ('email', 'password')
        }),
        ('Personal Info', {
            'fields': (
                'first_name',
                'last_name',
                'phone_number',
                'phone_verified',
                'avatar',
                'preferred_language',
            )
        }),
        ('Permissions', {
            'fields': (
                'is_active',
                'is_staff',
                'is_superuser',
                'groups',
                'user_permissions',
            )
        }),
        ('Security', {
            'fields': (
                'last_login_ip',
                'failed_login_attempts',
                'locked_until',
            ),
            'classes': ('collapse',)
        }),
        ('Important dates', {
            'fields': ('last_login', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    readonly_fields = ['created_at', 'updated_at', 'last_login']

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'email',
                'password1',
                'password2',
                'first_name',
                'last_name',
                'phone_number',
                'preferred_language',
                'is_active',
                'is_staff',
            ),
        }),
    )


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Admin for UserProfile model."""

    list_display = [
        'user',
        'loyalty_points',
        'total_orders',
        'total_spent',
        'email_notifications',
        'created_at',
    ]

    list_filter = [
        'email_notifications',
        'sms_notifications',
        'push_notifications',
        'created_at',
    ]

    search_fields = [
        'user__email',
        'user__first_name',
        'user__last_name',
    ]

    readonly_fields = ['created_at', 'updated_at']
