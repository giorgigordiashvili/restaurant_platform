"""
Tenant-admin page for the first-login setup wizard (see apps.tenants.setup).

One URL, ``/tenant-admin/tenants/restaurantsetup/?step=<key>``, renders the
current step; each step posts to its own endpoint below and moves on.
"""

from __future__ import annotations

from datetime import time

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.core import modules
from apps.core.tenant_admin_base import (
    UNFOLD_INPUT_CLASSES,
    UNFOLD_TEXTAREA_CLASSES,
    TenantModelAdmin,
    has_resource_permission,
)
from apps.tenants import setup
from apps.tenants.models import Restaurant, RestaurantCategory, RestaurantHours, RestaurantSetup

_URL = "tenants_restaurantsetup"


class _StyledForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            cls = UNFOLD_TEXTAREA_CLASSES if isinstance(w, forms.Textarea) else UNFOLD_INPUT_CLASSES
            if isinstance(w, forms.CheckboxInput):
                continue
            w.attrs["class"] = (w.attrs.get("class", "") + " " + cls).strip()


class DetailsForm(_StyledForm):
    category = forms.ModelChoiceField(
        queryset=RestaurantCategory.objects.filter(is_active=True), required=False, label=_("Cuisine / category")
    )

    class Meta:
        model = Restaurant
        fields = [
            "name",
            "description",
            "category",
            "phone",
            "email",
            "website",
            "address",
            "city",
            "country",
            "timezone",
            "default_currency",
            "default_language",
        ]


class BrandingForm(_StyledForm):
    class Meta:
        model = Restaurant
        fields = ["logo", "cover_image", "primary_color"]
        widgets = {"primary_color": forms.TextInput(attrs={"type": "color"})}


class SetupTenantAdmin(TenantModelAdmin):
    permission_resource = "settings"
    restaurant_field = None
    change_list_template = "admin/tenants/restaurantsetup/change_list.html"
    list_display = ["name"]

    def get_queryset(self, request):
        restaurant = getattr(request, "restaurant", None)
        qs = super().get_queryset(request)
        return qs.filter(pk=restaurant.pk) if restaurant else qs.none()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        raise PermissionDenied

    # ── page ──────────────────────────────────────────────────────────────

    def changelist_view(self, request, extra_context=None):
        restaurant = getattr(request, "restaurant", None)
        if not restaurant or not self.has_view_permission(request):
            raise PermissionDenied
        step = setup.get_step(restaurant, request.GET.get("step"))
        context = {
            **self.admin_site.each_context(request),
            "title": _("Setup"),
            "restaurant": restaurant,
            "step": step,
            "steps": self._step_links(restaurant, step),
            "progress": setup.progress(restaurant, step.key),
            "can_manage": has_resource_permission(request, "settings", "update"),
            "u": {
                n: reverse(f"tenant_admin:{_URL}_{n}")
                for n in ("select", "details", "branding", "hours", "finish", "later", "restart")
            },
            "page_url": reverse(f"tenant_admin:{_URL}_changelist"),
            "next_url": reverse(f"tenant_admin:{_URL}_changelist")
            + f"?step={setup.next_step(restaurant, step.key).key}",
        }
        getattr(self, f"_ctx_{step.kind}")(request, restaurant, step, context)
        request.current_app = self.admin_site.name
        return render(request, self.change_list_template, context)

    def _step_links(self, restaurant, current):
        done = set(setup.state(restaurant).get("done_steps", []))
        base = reverse(f"tenant_admin:{_URL}_changelist")
        out = []
        reached = True
        for s in setup.steps(restaurant):
            out.append(
                {
                    "key": s.key,
                    "title": s.title,
                    "current": s.key == current.key,
                    "done": s.key in done,
                    "url": f"{base}?step={s.key}" if (s.key in done or reached) else None,
                }
            )
            if s.key == current.key:
                reached = False
        return out

    def _ctx_welcome(self, request, restaurant, step, context):
        context["groups"] = setup.groups(restaurant)

    def _ctx_details(self, request, restaurant, step, context):
        context["form"] = context.get("form") or DetailsForm(instance=restaurant)

    def _ctx_branding(self, request, restaurant, step, context):
        context["form"] = context.get("form") or BrandingForm(instance=restaurant)

    def _ctx_hours(self, request, restaurant, step, context):
        rows = {h.day_of_week: h for h in restaurant.operating_hours.all()}
        context["days"] = [{"idx": i, "label": label, "row": rows.get(i)} for i, label in RestaurantHours.DAY_CHOICES]

    def _ctx_module(self, request, restaurant, step, context):
        m = step.module
        on = modules.is_enabled(restaurant, m.code)
        options = []
        for name in (*m.sub_flags, *m.sub_fields):
            field = restaurant._meta.get_field(name)
            options.append(
                {
                    "name": name,
                    "label": str(field.verbose_name).capitalize(),
                    "help": str(field.help_text),
                    "is_bool": name in m.sub_flags,
                    "value": getattr(restaurant, name),
                }
            )
        extra = [
            modules.get(c).title
            for c in setup.with_requirements([m.code])
            if c != m.code and not modules.is_enabled(restaurant, c)
        ]
        context.update(
            {
                "module": m,
                "enabled": on,
                "options": options,
                "also_on": extra,
                "decision": setup.state(restaurant).get("decisions", {}).get(m.code),
                "landing": setup.landing_url(m.code),
                "enable_url": reverse(f"tenant_admin:{_URL}_enable", args=[m.code]),
                "skip_url": reverse(f"tenant_admin:{_URL}_skip", args=[m.code]),
            }
        )

    def _ctx_done(self, request, restaurant, step, context):
        base = settings.FRONTEND_BASE_URL.rstrip("/")
        context.update(
            {
                **setup.summary(restaurant),
                "public_url": f"{base}/{restaurant.default_language}/restaurant/{restaurant.slug}",
                "pos_url": settings.POS_BASE_URL,
                "menu_url": reverse("tenant_admin:menu_menuitem_changelist"),
                "staff_url": reverse("tenant_admin:staff_staffinvitation_add"),
                "modules_url": reverse("tenant_admin:tenants_restaurantmodules_changelist"),
                "finished": bool(setup.state(restaurant).get("finished_at")),
            }
        )

    # ── posts ─────────────────────────────────────────────────────────────

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [
            path("select/", wrap(require_POST(self.select_view)), name=f"{_URL}_select"),
            path("details/", wrap(require_POST(self.details_view)), name=f"{_URL}_details"),
            path("branding/", wrap(require_POST(self.branding_view)), name=f"{_URL}_branding"),
            path("hours/", wrap(require_POST(self.hours_view)), name=f"{_URL}_hours"),
            path("module/<slug:code>/enable/", wrap(require_POST(self.enable_view)), name=f"{_URL}_enable"),
            path("module/<slug:code>/skip/", wrap(require_POST(self.skip_view)), name=f"{_URL}_skip"),
            path("finish/", wrap(require_POST(self.finish_view)), name=f"{_URL}_finish"),
            path("later/", wrap(require_POST(self.later_view)), name=f"{_URL}_later"),
            path("restart/", wrap(require_POST(self.restart_view)), name=f"{_URL}_restart"),
        ]
        return custom + super().get_urls()

    def _guard(self, request):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "settings", "update"):
            raise PermissionDenied
        return request.restaurant

    def _go(self, step_key):
        return redirect(reverse(f"tenant_admin:{_URL}_changelist") + f"?step={step_key}")

    def select_view(self, request):
        restaurant = self._guard(request)
        setup.apply_selection(restaurant, request.POST.getlist("modules"), by=request.user)
        return self._go(setup.advance(restaurant, "welcome").key)

    def details_view(self, request):
        restaurant = self._guard(request)
        form = DetailsForm(request.POST, instance=restaurant)
        if not form.is_valid():
            return self._rerender(request, restaurant, "details", form)
        form.save()
        return self._go(setup.advance(restaurant, "details").key)

    def branding_view(self, request):
        restaurant = self._guard(request)
        if request.POST.get("skip"):
            return self._go(setup.advance(restaurant, "branding").key)
        form = BrandingForm(request.POST, request.FILES, instance=restaurant)
        if not form.is_valid():
            return self._rerender(request, restaurant, "branding", form)
        form.save()
        return self._go(setup.advance(restaurant, "branding").key)

    def hours_view(self, request):
        restaurant = self._guard(request)
        for idx, _label in RestaurantHours.DAY_CHOICES:
            row, _created = RestaurantHours.objects.get_or_create(
                restaurant=restaurant, day_of_week=idx, defaults={"open_time": time(9, 0), "close_time": time(22, 0)}
            )
            row.is_closed = bool(request.POST.get(f"closed_{idx}"))
            row.open_time = _parse_time(request.POST.get(f"open_{idx}")) or row.open_time
            row.close_time = _parse_time(request.POST.get(f"close_{idx}")) or row.close_time
            row.open_time_2 = _parse_time(request.POST.get(f"open2_{idx}"))
            row.close_time_2 = _parse_time(request.POST.get(f"close2_{idx}"))
            if not (row.open_time_2 and row.close_time_2):
                row.open_time_2 = row.close_time_2 = None
            row.save()
        return self._go(setup.advance(restaurant, "hours").key)

    def enable_view(self, request, code):
        restaurant = self._guard(request)
        m = modules.MODULES_BY_CODE.get(code)
        if m is None or not m.switchable:
            raise PermissionDenied
        try:
            setup.decide(restaurant, code, "enabled", by=request.user)
        except modules.ModuleError as exc:
            messages.error(request, " ".join(exc.messages))
            return self._go(f"module:{code}")
        data = {name: bool(request.POST.get(name)) for name in m.sub_flags}
        data.update({name: request.POST.get(name, "").strip() for name in m.sub_fields})
        if data:
            try:
                modules.set_options(restaurant, code, data, by=request.user)
            except Exception as exc:  # keep going: options can be fixed on the Modules page
                messages.warning(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        nxt = setup.advance(restaurant, f"module:{code}")
        landing = setup.landing_url(code) if not request.POST.get("stay") else None
        if landing:
            back = reverse(f"tenant_admin:{_URL}_changelist") + f"?step={nxt.key}"
            messages.info(
                request,
                format_html(
                    '{} <a href="{}" class="font-semibold underline">{}</a>',
                    _("%(module)s is on. When you are done here,") % {"module": m.title},
                    back,
                    _("continue the setup →"),
                ),
            )
            return redirect(landing)
        return self._go(nxt.key)

    def skip_view(self, request, code):
        restaurant = self._guard(request)
        if code not in modules.MODULES_BY_CODE:
            raise PermissionDenied
        setup.decide(restaurant, code, "skipped", by=request.user)
        return self._go(setup.advance(restaurant, f"module:{code}").key)

    def finish_view(self, request):
        restaurant = self._guard(request)
        setup.finish(restaurant)
        messages.success(request, _("Setup complete. Welcome aboard!"))
        return redirect("tenant_admin:index")

    def later_view(self, request):
        restaurant = self._guard(request)
        setup.defer(restaurant)
        messages.info(request, _("You can pick the setup up again any time from Settings → Setup wizard."))
        return redirect("tenant_admin:index")

    def restart_view(self, request):
        restaurant = self._guard(request)
        setup.restart(restaurant)
        return self._go("welcome")

    def _rerender(self, request, restaurant, key, form):
        request.GET = request.GET.copy()
        request.GET["step"] = key
        return self._render_with_form(request, form)

    def _render_with_form(self, request, form):
        restaurant = request.restaurant
        step = setup.get_step(restaurant, request.GET.get("step"))
        context = {
            **self.admin_site.each_context(request),
            "title": _("Setup"),
            "restaurant": restaurant,
            "step": step,
            "steps": self._step_links(restaurant, step),
            "progress": setup.progress(restaurant, step.key),
            "can_manage": True,
            "u": {
                n: reverse(f"tenant_admin:{_URL}_{n}")
                for n in ("select", "details", "branding", "hours", "finish", "later", "restart")
            },
            "page_url": reverse(f"tenant_admin:{_URL}_changelist"),
            "next_url": reverse(f"tenant_admin:{_URL}_changelist")
            + f"?step={setup.next_step(restaurant, step.key).key}",
            "form": form,
        }
        request.current_app = self.admin_site.name
        return render(request, self.change_list_template, context, status=400)


def _parse_time(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        hh, mm = value.split(":")[:2]
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None


def register_setup_admin(site):
    site.register(RestaurantSetup, SetupTenantAdmin)
