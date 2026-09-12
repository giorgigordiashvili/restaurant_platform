"""Tenant admin: customers (list + timeline), segments, campaigns (preview / test / send / schedule), automations."""

from __future__ import annotations

import json

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from unfold.decorators import action, display

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantModelAdmin, has_resource_permission
from apps.crm import services
from apps.crm.models import Automation, Campaign, Customer, Segment


class CrmEnabledMixin(ModuleEnabledMixin):
    module_code = "crm"


class CustomerForm(forms.ModelForm):
    tags_text = forms.CharField(required=False, label=_("Tags"), help_text=_("Comma separated, e.g. vip, corporate."))

    class Meta:
        model = Customer
        fields = ["name", "phone", "email", "birthday", "language", "notes", "marketing_opt_in"]
        widgets = {"birthday": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["tags_text"].initial = ", ".join(self.instance.tags or [])

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.tags = [t.strip() for t in (self.cleaned_data.get("tags_text") or "").split(",") if t.strip()]
        if commit:
            obj.save()
        return obj


class CustomerTenantAdmin(CrmEnabledMixin, TenantModelAdmin):
    permission_resource = "crm"
    restaurant_field = "restaurant"
    form = CustomerForm
    change_form_template = "admin/crm/customer/change_form.html"
    list_display = ["name", "phone", "email", "visits", "total_spend", "last_visit", "opt_in", "tag_list"]
    list_filter = ["marketing_opt_in", "source", "language"]
    search_fields = ["name", "phone", "email", "notes"]
    ordering = ["-last_visit_at"]
    readonly_fields = [
        "visits",
        "orders_count",
        "reservations_count",
        "reviews_count",
        "total_spend",
        "avg_ticket",
        "first_seen_at",
        "last_visit_at",
        "last_order_at",
        "last_rating",
        "opt_in_at",
        "opt_in_source",
        "opt_out_at",
        "source",
    ]
    fieldsets = (
        (None, {"fields": ("name", "phone", "email", "birthday", "language", "tags_text", "notes")}),
        (_("Consent"), {"fields": ("marketing_opt_in", "opt_in_at", "opt_in_source", "opt_out_at")}),
        (
            _("History"),
            {
                "fields": (
                    "visits",
                    "orders_count",
                    "reservations_count",
                    "reviews_count",
                    "total_spend",
                    "avg_ticket",
                    "first_seen_at",
                    "last_visit_at",
                    "last_order_at",
                    "last_rating",
                    "source",
                )
            },
        ),
    )
    actions = ["rebuild_stats", "add_tag_vip"]

    @display(description=_("Last visit"))
    def last_visit(self, obj):
        return obj.last_visit_at.strftime("%d.%m.%Y") if obj.last_visit_at else "—"

    @display(description=_("Consent"), boolean=True)
    def opt_in(self, obj):
        return obj.marketing_opt_in

    @display(description=_("Tags"))
    def tag_list(self, obj):
        return ", ".join(obj.tags or [])

    def save_model(self, request, obj, form, change):
        if not obj.restaurant_id:
            obj.restaurant = request.restaurant
        if change and "marketing_opt_in" in form.changed_data:
            old = Customer.objects.filter(pk=obj.pk).values_list("marketing_opt_in", flat=True).first()
            if old != obj.marketing_opt_in:
                obj.opt_in_at = timezone.now() if obj.marketing_opt_in else obj.opt_in_at
                obj.opt_in_source = "staff" if obj.marketing_opt_in else obj.opt_in_source
                obj.opt_out_at = None if obj.marketing_opt_in else timezone.now()
        super().save_model(request, obj, form, change)

    def rebuild_stats(self, request, queryset):
        for c in queryset:
            services.rebuild(c)
        messages.success(request, _("Statistics rebuilt for %(n)d customer(s).") % {"n": queryset.count()})

    rebuild_stats.short_description = _("Rebuild statistics")

    def add_tag_vip(self, request, queryset):
        for c in queryset:
            if "vip" not in (c.tags or []):
                c.tags = [*(c.tags or []), "vip"]
                c.save(update_fields=["tags", "updated_at"])

    add_tag_vip.short_description = _("Tag as VIP")

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = dict(extra_context or {})
        c = Customer.objects.filter(pk=object_id, restaurant=getattr(request, "restaurant", None)).first()
        if c is not None:
            extra_context["timeline"] = self._timeline(c)
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    def _timeline(self, c: Customer):
        from django.db.models import Q

        from apps.orders.models import Order
        from apps.reservations.models import Reservation
        from apps.reviews.models import Review

        items = []
        who = Q(pk__in=[])
        if c.user_id:
            who |= Q(customer_id=c.user_id)
        if c.phone:
            who |= Q(customer_phone=c.phone)
        if c.user_id or c.phone:
            for o in Order.objects.filter(who, restaurant=c.restaurant).order_by("-created_at")[:30]:
                items.append(
                    {
                        "when": o.created_at,
                        "kind": "order",
                        "title": f"#{o.order_number} · {o.get_status_display()} · {o.total}",
                        "url": reverse("tenant_admin:orders_order_change", args=[o.pk]),
                    }
                )
            rq = Q(pk__in=[])
            if c.user_id:
                rq |= Q(customer_id=c.user_id)
            if c.phone:
                rq |= Q(guest_phone=c.phone)
            for r in Reservation.objects.filter(rq, restaurant=c.restaurant).order_by("-reservation_date")[:20]:
                items.append(
                    {
                        "when": r.created_at,
                        "kind": "reservation",
                        "title": f"{r.reservation_date:%d.%m.%Y} {r.reservation_time:%H:%M} · {r.party_size} · {r.get_status_display()}",
                        "url": reverse("tenant_admin:reservations_reservation_change", args=[r.pk]),
                    }
                )
        if c.user_id:
            for rv in Review.objects.filter(restaurant=c.restaurant, user_id=c.user_id).order_by("-created_at")[:10]:
                items.append(
                    {
                        "when": rv.created_at,
                        "kind": "review",
                        "title": f"{'★' * int(rv.rating)} {rv.title or rv.body[:60]}",
                        "url": reverse("tenant_admin:reviews_review_change", args=[rv.pk]),
                    }
                )
        for d in c.campaign_deliveries.select_related("campaign").order_by("-created_at")[:10]:
            items.append(
                {"when": d.created_at, "kind": "campaign", "title": f"{d.campaign.name} · {d.status}", "url": ""}
            )
        for s in c.automation_sends.select_related("automation").order_by("-created_at")[:10]:
            items.append(
                {"when": s.created_at, "kind": "automation", "title": s.automation.get_kind_display(), "url": ""}
            )
        items.sort(key=lambda i: i["when"], reverse=True)
        return items[:60]


class SegmentForm(forms.ModelForm):
    rules_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5}),
        required=False,
        label=_("Rules (JSON)"),
        help_text=Segment._meta.get_field("rules").help_text,
    )

    class Meta:
        model = Segment
        fields = ["name", "description", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["rules_text"].initial = json.dumps(self.instance.rules or {}, ensure_ascii=False)

    def clean_rules_text(self):
        raw = (self.cleaned_data.get("rules_text") or "").strip() or "{}"
        try:
            data = json.loads(raw)
        except ValueError:
            raise forms.ValidationError(_('Rules must be valid JSON, e.g. {"min_visits": 3}'))
        if not isinstance(data, dict):
            raise forms.ValidationError(_("Rules must be a JSON object."))
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.rules = self.cleaned_data.get("rules_text") or {}
        if commit:
            obj.save()
        return obj


class SegmentTenantAdmin(CrmEnabledMixin, TenantModelAdmin):
    permission_resource = "crm"
    restaurant_field = "restaurant"
    form = SegmentForm
    list_display = ["name", "description", "size", "is_builtin", "is_active"]
    list_filter = ["is_active", "is_builtin"]
    search_fields = ["name"]

    @display(description=_("Guests now"))
    def size(self, obj):
        return services.segment_count(obj)

    def save_model(self, request, obj, form, change):
        if not obj.restaurant_id:
            obj.restaurant = request.restaurant
        super().save_model(request, obj, form, change)


class CampaignForm(forms.ModelForm):
    class Meta:
        model = Campaign
        fields = ["name", "channel", "segment", "subject", "body", "promotion", "scheduled_at"]
        widgets = {"scheduled_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}


class CampaignTenantAdmin(CrmEnabledMixin, TenantModelAdmin):
    permission_resource = "crm"
    restaurant_field = "restaurant"
    form = CampaignForm
    change_form_template = "admin/crm/campaign/change_form.html"
    list_display = [
        "name",
        "channel",
        "segment",
        "status_badge",
        "audience_count",
        "sent_count",
        "failed_count",
        "skipped_count",
        "scheduled_at",
        "finished_at",
    ]
    list_filter = ["status", "channel"]
    search_fields = ["name", "body"]
    actions_row = ["send_now", "cancel"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        restaurant = getattr(request, "restaurant", None)
        if "segment" in form.base_fields:
            form.base_fields["segment"].queryset = Segment.objects.filter(restaurant=restaurant, is_active=True)
        if "promotion" in form.base_fields:
            from apps.promotions.models import Promotion

            form.base_fields["promotion"].queryset = Promotion.objects.filter(
                restaurant=restaurant, kind="promo_code", is_active=True
            )
        return form

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status not in ("draft", "scheduled"):
            return ["name", "channel", "segment", "subject", "body", "promotion", "scheduled_at"]
        return []

    @display(
        description=_("Status"),
        label={"draft": "info", "scheduled": "warning", "sending": "warning", "sent": "success", "cancelled": "danger"},
    )
    def status_badge(self, obj):
        return obj.status

    def save_model(self, request, obj, form, change):
        if not obj.restaurant_id:
            obj.restaurant = request.restaurant
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if obj.status == "draft" and obj.scheduled_at and obj.scheduled_at > timezone.now():
            services.start_campaign(obj, by=request.user, when=obj.scheduled_at)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = dict(extra_context or {})
        c = (
            Campaign.objects.filter(pk=object_id, restaurant=getattr(request, "restaurant", None))
            .select_related("segment", "promotion")
            .first()
        )
        if c is not None:
            n = "tenant_admin:crm_campaign_"
            extra_context.update(
                {
                    "campaign": c,
                    "audience": services.audience(c).count(),
                    "preview_text": services.preview(c),
                    "test_url": reverse(f"{n}test", args=[c.pk]),
                    "send_url": reverse(f"{n}send_now", args=[c.pk]),
                    "can_send": has_resource_permission(request, "crm", "update")
                    and c.status in ("draft", "scheduled"),
                }
            )
        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    def get_urls(self):
        wrap = self.admin_site.admin_view
        custom = [path("<uuid:object_id>/test/", wrap(require_POST(self.test_view)), name="crm_campaign_test")]
        return custom + super().get_urls()

    def _campaign(self, request, object_id):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "crm", "update"):
            raise PermissionDenied
        return get_object_or_404(Campaign, pk=object_id, restaurant=request.restaurant)

    def test_view(self, request, object_id):
        c = self._campaign(request, object_id)
        to = request.POST.get("to", "").strip()
        if not to:
            messages.error(request, _("Enter a phone number or email for the test."))
        else:
            msg = services.send_test(c, to, by=request.user)
            msg.refresh_from_db()
            messages.info(
                request,
                _("Test %(status)s: %(to)s %(error)s")
                % {"status": msg.get_status_display(), "to": msg.to, "error": msg.error},
            )
        return redirect("tenant_admin:crm_campaign_change", object_id=c.pk)

    @action(description=_("Send now"), url_path="send-now")
    def send_now(self, request, object_id):
        c = self._campaign(request, object_id)
        try:
            services.start_campaign(c, by=request.user)
            messages.success(
                request, _("'%(name)s' is being sent to %(n)d guests.") % {"name": c.name, "n": c.audience_count}
            )
        except services.CrmError as exc:
            messages.error(request, exc.message)
        return redirect("tenant_admin:crm_campaign_changelist")

    @action(description=_("Cancel"), url_path="cancel")
    def cancel(self, request, object_id):
        c = self._campaign(request, object_id)
        services.cancel_campaign(c)
        messages.success(request, _("'%(name)s' cancelled.") % {"name": c.name})
        return redirect("tenant_admin:crm_campaign_changelist")


class AutomationTenantAdmin(CrmEnabledMixin, TenantModelAdmin):
    permission_resource = "crm"
    restaurant_field = "restaurant"
    list_display = ["kind", "enabled", "channel", "promotion", "sent_count"]
    list_editable = ["enabled", "channel"]
    fields = ["kind", "enabled", "channel", "body", "promotion", "delay_hours", "lapsed_days", "sent_count"]
    readonly_fields = ["kind", "sent_count"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        restaurant = getattr(request, "restaurant", None)
        if restaurant is not None:
            for kind, _label in Automation.KIND_CHOICES:
                Automation.objects.get_or_create(restaurant=restaurant, kind=kind)
        return super().get_queryset(request)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "promotion" in form.base_fields:
            from apps.promotions.models import Promotion

            form.base_fields["promotion"].queryset = Promotion.objects.filter(
                restaurant=getattr(request, "restaurant", None), kind="promo_code", is_active=True
            )
        if "body" in form.base_fields and obj is not None and not obj.body:
            form.base_fields["body"].initial = obj.template()
        return form


def register_crm_admin(site):
    site.register(Customer, CustomerTenantAdmin)
    site.register(Segment, SegmentTenantAdmin)
    site.register(Campaign, CampaignTenantAdmin)
    site.register(Automation, AutomationTenantAdmin)
