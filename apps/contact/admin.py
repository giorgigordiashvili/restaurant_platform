from django.contrib import admin

from .models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "first_name", "last_name", "email", "topic", "is_handled")
    list_filter = ("is_handled", "created_at", "locale")
    search_fields = ("first_name", "last_name", "email", "phone", "topic", "message")
    readonly_fields = (
        "first_name",
        "last_name",
        "email",
        "phone",
        "topic",
        "message",
        "ip_address",
        "user_agent",
        "locale",
        "created_at",
    )
    list_per_page = 50
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    # Submissions are user-generated — admins can mark handled or delete,
    # but shouldn't edit the submitted content itself.
    def has_add_permission(self, request):
        return False
