"""Tenant admin: online ordering settings (+ hours, courier providers), delivery zones on a map, couriers, deliveries, custom domains."""

from __future__ import annotations

import json
from datetime import time
from decimal import Decimal, InvalidOperation

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from unfold.decorators import action, display

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantModelAdmin, has_resource_permission
from apps.delivery.courier import registry
from apps.delivery.models import RestaurantDeliveryPlatform
from apps.ordering import dispatch, domains, services
from apps.ordering.models import (
    Courier,
    Delivery,
    DeliveryZone,
    OnlineOrderingSettingsPage,
    RestaurantDomain,
)
from apps.tenants.models import RestaurantHours

COURIER_CREDENTIALS = {
    "wolt_drive": [
        ("api_key", _("Merchant API key"), True, _("Issued by Wolt Drive for your venue.")),
        ("merchant_id", _("Merchant id"), False, ""),
        ("client_secret", _("Webhook secret"), True, _("Wolt signs webhooks with it (JWT).")),
    ],
    "glovo_odr": [
        ("client_id", _("Client id"), False, ""),
        ("client_secret", _("Client secret"), True, ""),
        ("callback_secret", _("Callback secret"), True, _("Signs the status callbacks (X-Signature-SHA256).")),
        ("country", _("Country code"), False, _("ge")),
    ],
}
COURIER_STORE_LABELS = {"wolt_drive": _("Venue id at Wolt Drive"), "glovo_odr": _("Vendor id at Glovo")}
SETTINGS_BOOLS = [
    "pickup_enabled",
    "delivery_enabled",
    "asap_enabled",
    "scheduling_enabled",
    "pass_platform_fee_to_guest",
]
SETTINGS_INTS = {
    "lead_minutes": (5, 240),
    "delivery_extra_minutes": (0, 180),
    "slot_interval_minutes": (5, 120),
    "max_days_ahead": (0, 30),
    "cutoff_minutes_before_close": (0, 180),
}
SETTINGS_MONEY = ["min_order_pickup", "min_order_delivery", "free_delivery_over", "packaging_fee"]


class OrderingEnabledMixin(ModuleEnabledMixin):
    module_code = "online_ordering"


def _time(raw: str) -> time | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        h, m = raw.split(":")[:2]
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return None


class OnlineOrderingSettingsTenantAdmin(OrderingEnabledMixin, TenantModelAdmin):
    permission_resource = "settings"
    restaurant_field = "restaurant"
    change_list_template = "admin/ordering/onlineorderingsettingspage/change_list.html"
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
        hours = {h.day_of_week: h for h in restaurant.operating_hours.all()}
        days = [{"idx": idx, "label": label, "row": hours.get(idx)} for idx, label in RestaurantHours.DAY_CHOICES]
        n = "tenant_admin:ordering_onlineorderingsettingspage_"
        base = getattr(settings, "PUBLIC_API_BASE_URL", "").rstrip("/")
        labels = dict(RestaurantDeliveryPlatform.PLATFORM_CHOICES)
        couriers = []
        for code in RestaurantDeliveryPlatform.COURIERS:
            link, _created = RestaurantDeliveryPlatform.objects.get_or_create(
                restaurant=restaurant, platform=code, defaults={"is_enabled": False}
            )
            creds = link.get_credentials()
            couriers.append(
                {
                    "code": code,
                    "label": labels[code],
                    "link": link,
                    "configured": registry.is_configured(restaurant, code),
                    "store_label": COURIER_STORE_LABELS[code],
                    "fields": [
                        {"name": name, "label": flabel, "secret": secret, "hint": hint, "is_set": bool(creds.get(name))}
                        for name, flabel, secret, hint in COURIER_CREDENTIALS[code]
                    ],
                    "webhook_url": (
                        f"{base}{reverse('delivery:wolt-drive-webhook')}"
                        if code == "wolt_drive"
                        else f"{base}{reverse('delivery:glovo-odr-callback')}"
                    ),
                    "save_url": reverse(f"{n}courier_save", args=[code]),
                }
            )
        extra_context = dict(extra_context or {})
        extra_context.update(
            {
                "cfg": cfg,
                "days": days,
                "couriers": couriers,
                "paused": services.is_paused(cfg),
                "open_now": restaurant.is_open_now,
                "public": services.public_config(restaurant),
                "zones_count": DeliveryZone.objects.filter(restaurant=restaurant, is_active=True).count(),
                "has_location": restaurant.latitude is not None and restaurant.longitude is not None,
                "can_manage": has_resource_permission(request, "settings", "update"),
                "save_url": reverse(f"{n}save"),
                "hours_url": reverse(f"{n}hours"),
                "pause_url": reverse(f"{n}pause"),
                "resume_url": reverse(f"{n}resume"),
                "zones_url": reverse("tenant_admin:ordering_deliveryzone_changelist"),
                "domains_url": reverse("tenant_admin:ordering_restaurantdomain_changelist"),
                "provider_choices": cfg._meta.get_field("courier_provider").choices,
                "auto_choices": cfg._meta.get_field("auto_request_courier_on").choices,
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        wrap = self.admin_site.admin_view
        n = "ordering_onlineorderingsettingspage"
        custom = [
            path("save/", wrap(require_POST(self.save_view)), name=f"{n}_save"),
            path("hours/", wrap(require_POST(self.hours_view)), name=f"{n}_hours"),
            path("pause/", wrap(require_POST(self.pause_view)), name=f"{n}_pause"),
            path("resume/", wrap(require_POST(self.resume_view)), name=f"{n}_resume"),
            path("courier/<slug:code>/save/", wrap(require_POST(self.courier_save_view)), name=f"{n}_courier_save"),
        ]
        return custom + super().get_urls()

    def _guard(self, request):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "settings", "update"):
            raise PermissionDenied

    def _back(self):
        return redirect("tenant_admin:ordering_onlineorderingsettingspage_changelist")

    def save_view(self, request):
        self._guard(request)
        cfg = services.settings_for(request.restaurant)
        for name in SETTINGS_BOOLS:
            setattr(cfg, name, bool(request.POST.get(name)))
        for name, (lo, hi) in SETTINGS_INTS.items():
            try:
                setattr(cfg, name, max(lo, min(int(request.POST.get(name) or getattr(cfg, name)), hi)))
            except ValueError:
                pass
        for name in SETTINGS_MONEY:
            try:
                setattr(cfg, name, max(Decimal(request.POST.get(name) or "0"), Decimal("0")))
            except InvalidOperation:
                pass
        provider = request.POST.get("courier_provider", cfg.courier_provider)
        if provider in dict(cfg._meta.get_field("courier_provider").choices):
            cfg.courier_provider = provider
        auto = request.POST.get("auto_request_courier_on", cfg.auto_request_courier_on)
        if auto in dict(cfg._meta.get_field("auto_request_courier_on").choices):
            cfg.auto_request_courier_on = auto
        cfg.save()
        if (
            cfg.delivery_enabled
            and not DeliveryZone.objects.filter(restaurant=request.restaurant, is_active=True).exists()
        ):
            messages.warning(request, _("Delivery is on but you have no delivery zones yet -- add at least one."))
        if cfg.courier_provider != "own" and not registry.is_configured(request.restaurant, cfg.courier_provider):
            messages.warning(request, _("The courier service is not connected yet; fill in its keys below."))
        messages.success(request, _("Online ordering settings saved."))
        return self._back()

    def hours_view(self, request):
        self._guard(request)
        for idx, _label in RestaurantHours.DAY_CHOICES:
            closed = bool(request.POST.get(f"closed_{idx}"))
            open_t = _time(request.POST.get(f"open_{idx}")) or time(9, 0)
            close_t = _time(request.POST.get(f"close_{idx}")) or time(22, 0)
            RestaurantHours.objects.update_or_create(
                restaurant=request.restaurant,
                day_of_week=idx,
                defaults={
                    "open_time": open_t,
                    "close_time": close_t,
                    "open_time_2": _time(request.POST.get(f"open2_{idx}")),
                    "close_time_2": _time(request.POST.get(f"close2_{idx}")),
                    "is_closed": closed,
                },
            )
        messages.success(request, _("Opening hours saved."))
        return self._back()

    def pause_view(self, request):
        self._guard(request)
        try:
            minutes = max(1, min(int(request.POST.get("minutes") or 30), 24 * 60))
        except ValueError:
            minutes = 30
        services.pause(request.restaurant, minutes, request.POST.get("reason", ""), by=request.user)
        messages.success(request, _("Online orders paused for {p0} minutes.").format(p0=minutes))
        return self._back()

    def resume_view(self, request):
        self._guard(request)
        services.resume(request.restaurant, by=request.user)
        messages.success(request, _("Online orders resumed."))
        return self._back()

    def courier_save_view(self, request, code):
        self._guard(request)
        if code not in RestaurantDeliveryPlatform.COURIERS:
            raise PermissionDenied
        link, _created = RestaurantDeliveryPlatform.objects.get_or_create(
            restaurant=request.restaurant, platform=code, defaults={"is_enabled": False}
        )
        link.store_external_id = request.POST.get("store_external_id", "").strip()
        link.is_enabled = bool(request.POST.get("is_enabled"))
        link.sandbox = bool(request.POST.get("sandbox"))
        creds = {} if request.POST.get("clear_credentials") else link.get_credentials()
        if not request.POST.get("clear_credentials"):
            for name, _label, _secret, _hint in COURIER_CREDENTIALS[code]:
                value = request.POST.get(name, "").strip()
                if value:
                    creds[name] = value
        link.set_credentials(creds)
        link.save()
        if link.is_enabled and not registry.is_configured(request.restaurant, code):
            messages.warning(request, _("Saved, but some keys are still missing."))
        else:
            messages.success(request, _("Courier service saved."))
        return self._back()


class DeliveryZoneForm(forms.ModelForm):
    class Meta:
        model = DeliveryZone
        fields = [
            "name",
            "kind",
            "radius_km",
            "polygon",
            "fee",
            "min_order",
            "eta_minutes",
            "sort",
            "color",
            "is_active",
        ]
        widgets = {"polygon": forms.HiddenInput()}

    def clean(self):
        data = super().clean()
        if data.get("kind") == "polygon":
            poly = data.get("polygon") or []
            if isinstance(poly, str):
                try:
                    poly = json.loads(poly)
                except ValueError:
                    poly = []
            if len(poly) < 3:
                raise forms.ValidationError(_("Draw the zone on the map (at least three points)."))
            data["polygon"] = poly
        return data


class SettingsOwnedMixin:
    """Zones / domains are part of "settings": the role grants read + update, never create / delete."""

    def has_add_permission(self, request):
        return self._has_resource_permission(request, "update")

    def has_delete_permission(self, request, obj=None):
        return self._has_resource_permission(request, "update")


class DeliveryZoneTenantAdmin(OrderingEnabledMixin, SettingsOwnedMixin, TenantModelAdmin):
    permission_resource = "settings"
    restaurant_field = "restaurant"
    form = DeliveryZoneForm
    list_display = ["name", "kind", "size", "fee", "min_order", "eta_minutes", "sort", "is_active"]
    list_editable = ["sort", "is_active"]
    ordering = ["sort"]
    change_list_template = "admin/ordering/deliveryzone/change_list.html"
    change_form_template = "admin/ordering/deliveryzone/change_form.html"

    @display(description=_("Size"))
    def size(self, obj):
        if obj.kind == "polygon":
            return _("{p0} points").format(p0=len(obj.polygon or []))
        return f"{obj.radius_km} km"

    def _map_context(self, request):
        r = request.restaurant
        zones = [
            {
                "id": str(z.pk),
                "name": z.name,
                "kind": z.kind,
                "radius_km": float(z.radius_km),
                "polygon": z.polygon if z.kind == "polygon" else [],
                "fee": str(z.fee),
                "color": z.color or "",
                "is_active": z.is_active,
            }
            for z in DeliveryZone.objects.filter(restaurant=r)
        ]
        return {
            "map_json": json.dumps(
                {
                    "center": (
                        [float(r.latitude), float(r.longitude)]
                        if r.latitude is not None and r.longitude is not None
                        else [41.7151, 44.8271]
                    ),
                    "has_location": r.latitude is not None and r.longitude is not None,
                    "restaurant": r.name,
                    "zones": zones,
                }
            ),
            "settings_url": reverse("tenant_admin:tenants_restaurant_changelist"),
        }

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        if getattr(request, "restaurant", None):
            extra_context.update(self._map_context(request))
        return super().changelist_view(request, extra_context=extra_context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = dict(extra_context or {})
        if getattr(request, "restaurant", None):
            extra_context.update(self._map_context(request))
            extra_context["editing_id"] = str(object_id) if object_id else ""
        return super().changeform_view(request, object_id, form_url, extra_context)


class CourierTenantAdmin(OrderingEnabledMixin, TenantModelAdmin):
    permission_resource = "staff"
    restaurant_field = "restaurant"
    list_display = ["name", "phone", "vehicle", "staff", "is_available", "is_active", "open_deliveries"]
    list_editable = ["is_available", "is_active"]
    list_filter = ["is_active", "is_available"]
    search_fields = ["name", "phone"]
    fields = ["name", "phone", "vehicle", "staff", "is_active", "is_available"]

    @display(description=_("On the road"))
    def open_deliveries(self, obj):
        return obj.deliveries.filter(status__in=("assigned", "picked_up")).count()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "staff" and getattr(request, "restaurant", None):
            from apps.staff.models import StaffMember

            kwargs["queryset"] = StaffMember.objects.filter(
                restaurant=request.restaurant, is_active=True
            ).select_related("user")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class DeliveryTenantAdmin(OrderingEnabledMixin, TenantModelAdmin):
    permission_resource = "orders"
    restaurant_field = "restaurant"
    list_display = [
        "order_link",
        "provider",
        "status_badge",
        "courier_label",
        "eta",
        "cost",
        "fee_charged",
        "created_at",
    ]
    list_filter = ["status", "provider"]
    search_fields = ["order__order_number", "external_id", "courier_name", "order__customer_name"]
    ordering = ["-created_at"]
    actions_row = ["request_row", "assign_row", "cancel_row"]
    readonly_fields = [
        "order",
        "provider",
        "status",
        "courier",
        "courier_name",
        "courier_phone",
        "external_id",
        "tracking_url",
        "quote",
        "cost",
        "fee_charged",
        "pickup_eta",
        "dropoff_eta",
        "error",
        "events",
        "requested_at",
        "picked_up_at",
        "delivered_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description=_("Order"))
    def order_link(self, obj):
        return f"{obj.order.order_number} · {obj.order.customer_name or '—'}"

    @display(
        description=_("Status"),
        label={
            "pending": "info",
            "quoted": "info",
            "requested": "warning",
            "accepted": "warning",
            "assigned": "warning",
            "picked_up": "warning",
            "delivered": "success",
            "failed": "danger",
            "cancelled": "danger",
        },
    )
    def status_badge(self, obj):
        return obj.status

    @display(description=_("Courier"))
    def courier_label(self, obj):
        return obj.courier_name or (obj.courier.name if obj.courier_id else "—")

    @display(description=_("ETA"))
    def eta(self, obj):
        return timezone.localtime(obj.dropoff_eta).strftime("%H:%M") if obj.dropoff_eta else "—"

    def get_urls(self):
        wrap = self.admin_site.admin_view
        n = "ordering_delivery"
        custom = [path("<uuid:object_id>/assign/", wrap(self.assign_view), name=f"{n}_assign_page")]
        return custom + super().get_urls()

    def _row(self, request, object_id):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "orders", "update"):
            raise PermissionDenied
        return get_object_or_404(Delivery.objects.select_related("order"), pk=object_id, restaurant=request.restaurant)

    def _back(self):
        return redirect("tenant_admin:ordering_delivery_changelist")

    @action(description=_("Request courier"), url_path="request")
    def request_row(self, request, object_id):
        d = self._row(request, object_id)
        try:
            dispatch.request_courier(d.order, by=request.user)
            messages.success(request, _("Courier requested for {p0}.").format(p0=d.order.order_number))
        except dispatch.DispatchError as exc:
            messages.error(request, exc.message)
        return self._back()

    @action(description=_("Assign"), url_path="assign")
    def assign_row(self, request, object_id):
        return redirect("tenant_admin:ordering_delivery_assign_page", object_id=object_id)

    @action(description=_("Cancel"), url_path="cancel")
    def cancel_row(self, request, object_id):
        d = self._row(request, object_id)
        dispatch.cancel_courier(d, reason="Cancelled from admin", by=request.user)
        messages.success(request, _("Courier cancelled."))
        return self._back()

    def assign_view(self, request, object_id):
        d = self._row(request, object_id)
        couriers = Courier.objects.filter(restaurant=request.restaurant, is_active=True)
        if request.method == "POST":
            courier = get_object_or_404(Courier, pk=request.POST.get("courier_id"), restaurant=request.restaurant)
            try:
                dispatch.assign_own(d, courier, by=request.user)
                messages.success(request, _("{p0} assigned to {p1}.").format(p0=d.order.order_number, p1=courier.name))
            except dispatch.DispatchError as exc:
                messages.error(request, exc.message)
            return self._back()
        request.current_app = self.admin_site.name
        context = {
            **self.admin_site.each_context(request),
            "title": _("Assign courier · {p0}").format(p0=d.order.order_number),
            "delivery": d,
            "couriers": couriers,
            "back_url": reverse("tenant_admin:ordering_delivery_changelist"),
        }
        return render(request, "admin/ordering/delivery/assign.html", context)


class RestaurantDomainTenantAdmin(OrderingEnabledMixin, SettingsOwnedMixin, TenantModelAdmin):
    permission_resource = "settings"
    restaurant_field = "restaurant"
    list_display = ["domain", "is_primary", "verified", "last_check_at", "error"]
    fields = ["domain", "is_primary"]
    actions_row = ["verify_row"]
    change_list_template = "admin/ordering/restaurantdomain/change_list.html"

    @display(description=_("Verified"), boolean=True)
    def verified(self, obj):
        return obj.is_verified

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context.update(domains.expected_targets())
        return super().changelist_view(request, extra_context=extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.is_primary:
            RestaurantDomain.objects.filter(restaurant=obj.restaurant).exclude(pk=obj.pk).update(is_primary=False)
        if domains.verify(obj):
            messages.success(
                request, _("{p0} points at us -- HTTPS will be issued on the first visit.").format(p0=obj.domain)
            )
        else:
            messages.warning(request, _("Saved. DNS is not pointing at us yet: {p0}").format(p0=obj.error))

    @action(description=_("Verify"), url_path="verify")
    def verify_row(self, request, object_id):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "settings", "update"):
            raise PermissionDenied
        row = get_object_or_404(RestaurantDomain, pk=object_id, restaurant=request.restaurant)
        if domains.verify(row):
            messages.success(request, _("{p0} is verified.").format(p0=row.domain))
        else:
            messages.warning(request, row.error)
        return redirect("tenant_admin:ordering_restaurantdomain_changelist")


def register_ordering_admin(site):
    site.register(OnlineOrderingSettingsPage, OnlineOrderingSettingsTenantAdmin)
    site.register(DeliveryZone, DeliveryZoneTenantAdmin)
    site.register(Courier, CourierTenantAdmin)
    site.register(Delivery, DeliveryTenantAdmin)
    site.register(RestaurantDomain, RestaurantDomainTenantAdmin)
