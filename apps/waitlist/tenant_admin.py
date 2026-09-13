"""Tenant admin: today's queue with Notify / Seat / Left / No-show, and the settings page with the door QR."""

from __future__ import annotations

import base64
from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from unfold.decorators import action, display

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantModelAdmin, has_resource_permission
from apps.tables.models import Table
from apps.waitlist import services
from apps.waitlist.models import WaitlistEntry, WaitlistSettingsPage


class WaitlistEnabledMixin(ModuleEnabledMixin):
    module_code = "waitlist"


class WaitlistEntryTenantAdmin(WaitlistEnabledMixin, TenantModelAdmin):
    permission_resource = "reservations"
    restaurant_field = "restaurant"
    list_display = [
        "position",
        "name",
        "party_size",
        "phone",
        "quoted_minutes",
        "waited",
        "status_badge",
        "source",
        "table",
    ]
    list_filter = ["status", "source", "date"]
    search_fields = ["name", "phone"]
    ordering = ["-date", "position"]
    fields = ["name", "phone", "party_size", "quoted_minutes", "notes"]
    actions_row = ["notify_row", "seat_row", "left_row", "no_show_row"]
    change_list_template = "admin/waitlist/waitlistentry/change_list.html"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.GET.get("date__exact") and not request.GET.get("q") and not request.GET.get("status__exact"):
            restaurant = getattr(request, "restaurant", None)
            if restaurant is not None:
                qs = qs.filter(date=services.today(restaurant))
        return qs.select_related("table", "reservation")

    @display(description=_("Waited"))
    def waited(self, obj):
        return f"{obj.waited_minutes} min"

    @display(
        description=_("Status"),
        label={
            "waiting": "info",
            "notified": "warning",
            "seated": "success",
            "left": "danger",
            "cancelled": "danger",
            "no_show": "danger",
        },
    )
    def status_badge(self, obj):
        return obj.status

    def save_model(self, request, obj, form, change):
        if change:
            services.update_entry(obj, by=request.user)
            super().save_model(request, obj, form, change)
            return
        entry = services.add_entry(
            request.restaurant,
            name=obj.name,
            phone=obj.phone,
            party_size=obj.party_size,
            quoted=obj.quoted_minutes or None,
            notes=obj.notes,
            by=request.user,
        )
        obj.pk = entry.pk

    def response_add(self, request, obj, post_url_continue=None):
        return redirect("tenant_admin:waitlist_waitlistentry_changelist")

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        if getattr(request, "restaurant", None):
            extra_context["summary"] = services.summary(request.restaurant)
            extra_context["settings_url"] = reverse("tenant_admin:waitlist_waitlistsettingspage_changelist")
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        wrap = self.admin_site.admin_view
        n = "waitlist_waitlistentry"
        return [path("<uuid:object_id>/seat-page/", wrap(self.seat_page), name=f"{n}_seat_page")] + super().get_urls()

    def _entry(self, request, object_id):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "reservations", "update"):
            raise PermissionDenied
        return get_object_or_404(WaitlistEntry, pk=object_id, restaurant=request.restaurant)

    def _back(self):
        return redirect("tenant_admin:waitlist_waitlistentry_changelist")

    @action(description=_("Notify"), url_path="notify")
    def notify_row(self, request, object_id):
        try:
            services.notify_ready(self._entry(request, object_id), by=request.user)
            messages.success(request, _("Guest notified."))
        except services.WaitlistError as exc:
            messages.error(request, exc.message)
        return self._back()

    @action(description=_("Seat"), url_path="seat")
    def seat_row(self, request, object_id):
        return redirect("tenant_admin:waitlist_waitlistentry_seat_page", object_id=object_id)

    @action(description=_("Left"), url_path="left")
    def left_row(self, request, object_id):
        try:
            services.mark(self._entry(request, object_id), "left", by=request.user)
        except services.WaitlistError as exc:
            messages.error(request, exc.message)
        return self._back()

    @action(description=_("No-show"), url_path="no-show")
    def no_show_row(self, request, object_id):
        try:
            services.mark(self._entry(request, object_id), "no_show", by=request.user)
        except services.WaitlistError as exc:
            messages.error(request, exc.message)
        return self._back()

    def seat_page(self, request, object_id):
        entry = self._entry(request, object_id)
        tables = (
            Table.objects.filter(restaurant=request.restaurant, is_active=True)
            .select_related("section")
            .order_by("section__name", "number")
        )
        if request.method == "POST":
            table = get_object_or_404(Table, pk=request.POST.get("table_id"), restaurant=request.restaurant)
            try:
                services.seat(entry, table, by=request.user)
                messages.success(request, _("{p0} seated at table {p1}.").format(p0=entry.name, p1=table.number))
            except services.WaitlistError as exc:
                messages.error(request, exc.message)
            return self._back()
        request.current_app = self.admin_site.name
        context = {
            **self.admin_site.each_context(request),
            "title": _("Seat {p0} ({p1})").format(p0=entry.name, p1=entry.party_size),
            "entry": entry,
            "tables": tables,
            "back_url": reverse("tenant_admin:waitlist_waitlistentry_changelist"),
        }
        return render(request, "admin/waitlist/waitlistentry/seat.html", context)


class WaitlistSettingsTenantAdmin(WaitlistEnabledMixin, TenantModelAdmin):
    permission_resource = "settings"
    restaurant_field = "restaurant"
    change_list_template = "admin/waitlist/waitlistsettingspage/change_list.html"
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
        cfg = services.settings_for(request.restaurant)
        url = services.join_url(request.restaurant)
        from apps.tables.qr import render_qr_png

        qr = base64.b64encode(render_qr_png(url)).decode("ascii")
        n = "tenant_admin:waitlist_waitlistsettingspage_"
        extra_context = dict(extra_context or {})
        extra_context.update(
            {
                "cfg": cfg,
                "join_url": url,
                "qr_b64": qr,
                "can_manage": has_resource_permission(request, "settings", "update"),
                "save_url": reverse(f"{n}save"),
                "rotate_url": reverse(f"{n}rotate"),
                "queue_url": reverse("tenant_admin:waitlist_waitlistentry_changelist"),
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        wrap = self.admin_site.admin_view
        n = "waitlist_waitlistsettingspage"
        return [
            path("save/", wrap(require_POST(self.save_view)), name=f"{n}_save"),
            path("rotate/", wrap(require_POST(self.rotate_view)), name=f"{n}_rotate"),
        ] + super().get_urls()

    def _guard(self, request):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "settings", "update"):
            raise PermissionDenied

    def save_view(self, request):
        self._guard(request)
        cfg = services.settings_for(request.restaurant)
        for name in ("allow_self_join", "sms_on_join", "sms_on_ready"):
            setattr(cfg, name, bool(request.POST.get(name)))
        for name, lo, hi in (
            ("default_wait_minutes", 1, 240),
            ("notify_expire_minutes", 1, 120),
            ("max_party_size", 1, 50),
        ):
            try:
                setattr(cfg, name, max(lo, min(int(request.POST.get(name) or getattr(cfg, name)), hi)))
            except ValueError:
                pass
        cfg.save()
        messages.success(request, _("Waitlist settings saved."))
        return redirect("tenant_admin:waitlist_waitlistsettingspage_changelist")

    def rotate_view(self, request):
        self._guard(request)
        services.settings_for(request.restaurant).rotate_token()
        messages.success(request, _("New QR link generated -- reprint the sign at the door."))
        return redirect("tenant_admin:waitlist_waitlistsettingspage_changelist")


def register_waitlist_admin(site):
    site.register(WaitlistEntry, WaitlistEntryTenantAdmin)
    site.register(WaitlistSettingsPage, WaitlistSettingsTenantAdmin)
