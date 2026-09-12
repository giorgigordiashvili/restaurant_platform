"""Tenant admin: time entries (editable by managers), the weekly rota grid with publish / copy."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from unfold.decorators import display

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantModelAdmin, has_resource_permission
from apps.staff.models import StaffMember
from apps.timekeeping import services
from apps.timekeeping.models import RotaShift, TimeEntry


class TimekeepingEnabledMixin(ModuleEnabledMixin):
    module_code = "timekeeping"


class TimeEntryTenantAdmin(TimekeepingEnabledMixin, TenantModelAdmin):
    permission_resource = "timekeeping"
    restaurant_field = "restaurant"
    list_display = ["staff_member", "clock_in", "clock_out", "break_minutes", "hours", "source", "auto_closed"]
    list_filter = ["auto_closed", "source", "staff_member"]
    date_hierarchy = "clock_in"
    fields = ["staff_member", "clock_in", "clock_out", "break_minutes", "note", "source", "auto_closed", "edited_by"]
    readonly_fields = ["source", "edited_by"]
    ordering = ["-clock_in"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "staff_member" in form.base_fields:
            form.base_fields["staff_member"].queryset = StaffMember.objects.filter(
                restaurant=getattr(request, "restaurant", None)
            ).select_related("user")
        return form

    @display(description=_("Hours"))
    def hours(self, obj):
        return obj.worked_hours

    def save_model(self, request, obj, form, change):
        if not obj.restaurant_id:
            obj.restaurant = request.restaurant
        if change:
            obj.edited_by = request.user
            if obj.auto_closed and "clock_out" in form.changed_data:
                obj.auto_closed = False
            services._audit(
                "clock_edit",
                obj,
                request.user,
                f"Time entry of {obj.staff_member} edited: {', '.join(form.changed_data)}",
            )
        else:
            obj.source = "admin"
        super().save_model(request, obj, form, change)


class RotaShiftTenantAdmin(TimekeepingEnabledMixin, TenantModelAdmin):
    permission_resource = "timekeeping"
    restaurant_field = "restaurant"
    change_list_template = "admin/timekeeping/rotashift/change_list.html"
    list_display = ["date", "staff_member", "start_time", "end_time", "label", "published"]
    list_filter = ["published", "staff_member"]
    date_hierarchy = "date"
    fields = ["staff_member", "date", "start_time", "end_time", "label", "note", "published"]
    ordering = ["date", "start_time"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "staff_member" in form.base_fields:
            form.base_fields["staff_member"].queryset = StaffMember.objects.filter(
                restaurant=getattr(request, "restaurant", None), is_active=True
            ).select_related("user")
        return form

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if request.GET.get("date"):
            initial["date"] = request.GET["date"]
        if request.GET.get("staff_member"):
            initial["staff_member"] = request.GET["staff_member"]
        initial.setdefault("start_time", "10:00")
        initial.setdefault("end_time", "18:00")
        return initial

    def save_model(self, request, obj, form, change):
        if not obj.restaurant_id:
            obj.restaurant = request.restaurant
        if obj.published and not obj.published_at:
            obj.published_at = timezone.now()
        super().save_model(request, obj, form, change)

    def _monday(self, request):
        raw = request.GET.get("week") or request.POST.get("week") or ""
        try:
            day = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            day = timezone.localdate()
        return services.week_start(day)

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        restaurant = getattr(request, "restaurant", None)
        if restaurant:
            monday = self._monday(request)
            if "week" in request.GET:  # not a changelist filter: keep Django from treating it as a lookup
                request.GET = request.GET.copy()
                request.GET.pop("week")
            days = [monday + timedelta(days=i) for i in range(7)]
            members = list(
                StaffMember.objects.filter(restaurant=restaurant, is_active=True)
                .select_related("user", "role")
                .order_by("user__first_name", "user__email")
            )
            grid = {m.pk: {d: [] for d in days} for m in members}
            unpublished = 0
            for s in services.week_shifts(restaurant, monday):
                if s.staff_member_id in grid:
                    grid[s.staff_member_id][s.date].append(s)
                unpublished += int(not s.published)
            n = "tenant_admin:timekeeping_rotashift_"
            extra_context.update(
                {
                    "monday": monday,
                    "days": days,
                    "prev_week": (monday - timedelta(days=7)).isoformat(),
                    "next_week": (monday + timedelta(days=7)).isoformat(),
                    "rows": [
                        {
                            "member": m,
                            "name": m.user.get_full_name() or m.user.email,
                            "role": m.role.get_display_name() if m.role_id else "",
                            "cells": [{"date": d, "shifts": grid[m.pk][d]} for d in days],
                            "hours": sum((s.hours for d in days for s in grid[m.pk][d]), 0),
                        }
                        for m in members
                    ],
                    "unpublished": unpublished,
                    "can_manage": has_resource_permission(request, "timekeeping", "update"),
                    "publish_url": reverse(f"{n}publish"),
                    "copy_url": reverse(f"{n}copy_week"),
                    "add_url": reverse(f"{n}add"),
                    "change_url_name": f"{n}change",
                }
            )
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        wrap = self.admin_site.admin_view
        n = "timekeeping_rotashift"
        custom = [
            path("publish/", wrap(require_POST(self.publish_view)), name=f"{n}_publish"),
            path("copy-week/", wrap(require_POST(self.copy_view)), name=f"{n}_copy_week"),
        ]
        return custom + super().get_urls()

    def _guard(self, request):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "timekeeping", "update"):
            raise PermissionDenied

    def publish_view(self, request):
        self._guard(request)
        monday = self._monday(request)
        n = services.publish_week(request.restaurant, monday, by=request.user)
        messages.success(
            request,
            _("Published %(n)d shift(s) for the week of %(date)s.") % {"n": n, "date": monday.strftime("%d.%m")},
        )
        return redirect(f"{reverse('tenant_admin:timekeeping_rotashift_changelist')}?week={monday.isoformat()}")

    def copy_view(self, request):
        self._guard(request)
        monday = self._monday(request)
        n = services.copy_week(request.restaurant, monday - timedelta(days=7), monday, by=request.user)
        messages.success(request, _("Copied %(n)d shift(s) from the previous week.") % {"n": n})
        return redirect(f"{reverse('tenant_admin:timekeeping_rotashift_changelist')}?week={monday.isoformat()}")


def register_timekeeping_admin(site):
    site.register(TimeEntry, TimeEntryTenantAdmin)
    site.register(RotaShift, RotaShiftTenantAdmin)
