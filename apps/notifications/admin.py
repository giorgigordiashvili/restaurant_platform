from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import Device, Notification, OutboundMessage, RestaurantNotificationSettings, StaffNotificationPrefs


@admin.register(Device)
class DeviceAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["user", "restaurant", "kind", "platform", "is_active", "last_seen_at", "last_error"]
    list_filter = ["kind", "is_active"]
    search_fields = ["user__email", "token"]


@admin.register(Notification)
class NotificationAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["title", "event", "user", "restaurant", "read_at", "pushed_at", "push_error", "created_at"]
    list_filter = ["event"]
    search_fields = ["title", "user__email"]
    readonly_fields = [f.name for f in Notification._meta.fields if f.name != "id"]


@admin.register(StaffNotificationPrefs)
class StaffNotificationPrefsAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["user", "restaurant", "push", "email", "quiet_from", "quiet_to"]


@admin.register(RestaurantNotificationSettings)
class RestaurantNotificationSettingsAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["restaurant", "guest_sms", "guest_email", "sender_name"]


@admin.register(OutboundMessage)
class OutboundMessageAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["to", "channel", "kind", "status", "provider", "restaurant", "sent_at", "created_at"]
    list_filter = ["channel", "kind", "status"]
    search_fields = ["to", "subject"]
    readonly_fields = [f.name for f in OutboundMessage._meta.fields if f.name != "id"]
