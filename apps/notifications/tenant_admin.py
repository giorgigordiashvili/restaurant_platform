"""Tenant admin: the Notifications settings page (guest messaging + templates + who hears what) and the message log."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from unfold.decorators import action

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantModelAdmin, has_resource_permission
from apps.notifications import events, providers, services
from apps.notifications.models import (
    DEFAULT_TEMPLATES,
    Device,
    NotificationSettingsPage,
    OutboundMessage,
    StaffNotificationPrefs,
)

TEMPLATE_FIELDS = [
    ("reservation_confirmation", "Reservation confirmation"),
    ("reservation_reminder", "Reservation reminder"),
    ("review_prompt", "Review prompt (CRM)"),
    ("order_accepted", "Online order accepted"),
    ("order_ready", "Pickup order ready"),
    ("order_on_the_way", "Delivery on its way"),
    ("pay_link", "Payment link (card terminals)"),
    ("waitlist_joined", "Joined the waitlist"),
    ("table_ready", "Table ready (waitlist)"),
]


class NotificationSettingsTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    module_code = "notifications"
    permission_resource = "settings"
    restaurant_field = "restaurant"
    change_list_template = "admin/notifications/notificationsettingspage/change_list.html"
    list_display = ["id"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        raise PermissionDenied

    def changelist_view(self, request, extra_context=None):
        if not getattr(request, "restaurant", None) or not self.has_view_permission(request):
            raise PermissionDenied
        restaurant = request.restaurant
        cfg = services.settings_for(restaurant)
        from apps.staff.models import StaffMember

        members = list(StaffMember.objects.filter(restaurant=restaurant, is_active=True).select_related("user", "role"))
        prefs = {p.user_id: p for p in StaffNotificationPrefs.objects.filter(restaurant=restaurant)}
        devices = {}
        for d in Device.objects.filter(restaurant=restaurant, is_active=True):
            devices[d.user_id] = devices.get(d.user_id, 0) + 1
        people = []
        owner = restaurant.owner
        rows = [(owner, "Owner")] + [(m.user, m.role.get_display_name()) for m in members if m.user_id != owner.pk]
        for user, role in rows:
            p = prefs.get(user.pk)
            people.append(
                {
                    "user": user,
                    "role": role,
                    "push": p.push if p else True,
                    "email": p.email if p else False,
                    "muted": len(p.muted_events or []) if p else 0,
                    "devices": devices.get(user.pk, 0),
                }
            )
        n = "tenant_admin:notifications_notificationsettingspage_"
        extra_context = dict(extra_context or {})
        extra_context.update(
            {
                "cfg": cfg,
                "templates": [
                    {"key": k, "label": label, "ka": cfg.template(k, "ka"), "en": cfg.template(k, "en")}
                    for k, label in TEMPLATE_FIELDS
                ],
                "defaults": DEFAULT_TEMPLATES,
                "people": people,
                "events": events.groups(),
                "sms_provider": providers.sms_provider(),
                "sms_ok": providers.sms_configured(),
                "email_ok": providers.email_configured(),
                "can_manage": has_resource_permission(request, "settings", "update"),
                "save_url": reverse(f"{n}save"),
                "test_url": reverse(f"{n}test"),
                "messages_url": reverse("tenant_admin:notifications_outboundmessage_changelist"),
                "recent": list(OutboundMessage.objects.filter(restaurant=restaurant)[:8]),
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        wrap = self.admin_site.admin_view
        n = "notifications_notificationsettingspage"
        custom = [
            path("save/", wrap(require_POST(self.save_view)), name=f"{n}_save"),
            path("test/", wrap(require_POST(self.test_view)), name=f"{n}_test"),
        ]
        return custom + super().get_urls()

    def _guard(self, request):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "settings", "update"):
            raise PermissionDenied

    def save_view(self, request):
        self._guard(request)
        cfg = services.settings_for(request.restaurant)
        cfg.guest_sms = bool(request.POST.get("guest_sms"))
        cfg.guest_email = bool(request.POST.get("guest_email"))
        cfg.sender_name = request.POST.get("sender_name", "").strip()[:60]
        for key, _label in TEMPLATE_FIELDS:
            for lang in ("ka", "en"):
                value = request.POST.get(f"{key}_{lang}", "").strip()
                setattr(cfg, f"{key}_{lang}", value or DEFAULT_TEMPLATES[f"{key}_{lang}"])
        cfg.save()
        messages.success(request, _("Notification settings saved."))
        return redirect("tenant_admin:notifications_notificationsettingspage_changelist")

    def test_view(self, request):
        self._guard(request)
        channel = request.POST.get("channel", "sms")
        to = request.POST.get("to", "").strip()
        if channel not in ("sms", "email") or not to:
            messages.error(request, _("Choose SMS or email and enter a destination."))
            return redirect("tenant_admin:notifications_notificationsettingspage_changelist")
        msg = services.send_message(
            request.restaurant,
            channel,
            to,
            f"Test message from {request.restaurant.name} (AiMenu).",
            subject="AiMenu test",
            kind="test",
            by=request.user,
            force=True,
        )
        msg.refresh_from_db()
        if msg.status == "sent":
            messages.success(request, _("Test {p0} sent to {p1}.").format(p0=channel, p1=msg.to))
        else:
            messages.warning(request, f"Test {channel} {msg.status}: {msg.error or 'queued'}")
        return redirect("tenant_admin:notifications_notificationsettingspage_changelist")


class OutboundMessageTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    module_code = "notifications"
    permission_resource = "settings"
    restaurant_field = "restaurant"
    list_display = ["created_at", "channel", "to", "kind", "status", "provider", "error"]
    list_filter = ["channel", "kind", "status"]
    search_fields = ["to", "subject", "body"]
    readonly_fields = [f.name for f in OutboundMessage._meta.fields if f.name != "id"]
    actions_row = ["resend"]
    ordering = ["-created_at"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @action(description=_("Resend"), url_path="resend")
    def resend(self, request, object_id):
        msg = OutboundMessage.objects.filter(pk=object_id, restaurant=request.restaurant).first()
        if msg is None or not has_resource_permission(request, "settings", "update"):
            raise PermissionDenied
        from apps.core.enqueue import enqueue
        from apps.notifications import tasks

        msg.status = "queued"
        msg.error = ""
        msg.save(update_fields=["status", "error", "updated_at"])
        enqueue(tasks.deliver, str(msg.pk))
        messages.success(request, _("Message to {p0} queued again.").format(p0=msg.to))
        return redirect("tenant_admin:notifications_outboundmessage_changelist")


def register_notifications_admin(site):
    site.register(NotificationSettingsPage, NotificationSettingsTenantAdmin)
    site.register(OutboundMessage, OutboundMessageTenantAdmin)
