"""Delivery platforms page: one card per platform with credentials, webhook / menu URLs, tokens, menu push, recent events."""

from __future__ import annotations

import secrets

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import path, reverse
from django.views.decorators.http import require_POST

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantModelAdmin, has_resource_permission
from apps.delivery import services
from apps.delivery.models import DeliveryPlatformsPage, RestaurantDeliveryPlatform

IMPLEMENTED = {"glovo"}


class DeliveryPlatformsTenantAdmin(ModuleEnabledMixin, TenantModelAdmin):
    module_code = "delivery"
    permission_resource = "settings"
    restaurant_field = "restaurant"
    change_list_template = "admin/delivery/deliveryplatformspage/change_list.html"
    list_display = ["platform"]

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
        extra_context = dict(extra_context or {})
        extra_context.update(self._page_context(request))
        return super().changelist_view(request, extra_context=extra_context)

    def _page_context(self, request):
        restaurant = request.restaurant
        base = getattr(settings, "PUBLIC_API_BASE_URL", "").rstrip("/")
        cards = []
        for code, label in RestaurantDeliveryPlatform.PLATFORM_CHOICES:
            link, _ = RestaurantDeliveryPlatform.objects.get_or_create(
                restaurant=restaurant, platform=code, defaults={"is_enabled": False}
            )
            creds = link.get_credentials()
            cards.append(
                {
                    "link": link,
                    "code": code,
                    "label": label,
                    "implemented": code in IMPLEMENTED,
                    "has_token": bool(creds.get("api_token")) or bool(getattr(settings, "GLOVO_API_TOKEN", "")),
                    "webhook_url": f"{base}{reverse('delivery:glovo-orders')}" if code == "glovo" else "",
                    "cancel_url": (
                        f"{base}/api/v1/delivery/glovo/orders/{{order_id}}/cancel/" if code == "glovo" else ""
                    ),
                    "menu_url": services.menu_feed_url(link) if code == "glovo" and link.menu_token else "",
                    "last_sync": link.menu_syncs.first(),
                    "events": list(link.events.select_related("order")[:10]),
                    "webhook_token_once": request.session.pop(f"delivery:wt:{link.pk}", None),
                    "urls": {
                        "save": reverse("tenant_admin:delivery_deliveryplatformspage_save", args=[code]),
                        "rotate_webhook": reverse(
                            "tenant_admin:delivery_deliveryplatformspage_rotate_webhook", args=[code]
                        ),
                        "rotate_menu": reverse("tenant_admin:delivery_deliveryplatformspage_rotate_menu", args=[code]),
                        "push_menu": reverse("tenant_admin:delivery_deliveryplatformspage_push_menu", args=[code]),
                    },
                }
            )
        return {"cards": cards, "can_manage": has_resource_permission(request, "settings", "update")}

    def get_urls(self):
        wrap = self.admin_site.admin_view
        n = "delivery_deliveryplatformspage"
        custom = [
            path("<slug:code>/save/", wrap(require_POST(self.save_view)), name=f"{n}_save"),
            path(
                "<slug:code>/rotate-webhook-token/",
                wrap(require_POST(self.rotate_webhook_view)),
                name=f"{n}_rotate_webhook",
            ),
            path("<slug:code>/rotate-menu-token/", wrap(require_POST(self.rotate_menu_view)), name=f"{n}_rotate_menu"),
            path("<slug:code>/push-menu/", wrap(require_POST(self.push_menu_view)), name=f"{n}_push_menu"),
        ]
        return custom + super().get_urls()

    def _link(self, request, code):
        if (
            not getattr(request, "restaurant", None)
            or code not in dict(RestaurantDeliveryPlatform.PLATFORM_CHOICES)
            or not has_resource_permission(request, "settings", "update")
        ):
            raise PermissionDenied
        link, _ = RestaurantDeliveryPlatform.objects.get_or_create(
            restaurant=request.restaurant, platform=code, defaults={"is_enabled": False}
        )
        return link

    def _back(self):
        return redirect("tenant_admin:delivery_deliveryplatformspage_changelist")

    def save_view(self, request, code):
        link = self._link(request, code)
        link.store_external_id = request.POST.get("store_external_id", "").strip()
        link.is_enabled = bool(request.POST.get("is_enabled"))
        link.auto_accept = bool(request.POST.get("auto_accept"))
        link.sandbox = bool(request.POST.get("sandbox"))
        try:
            link.prep_time_minutes = max(1, min(int(request.POST.get("prep_time_minutes") or 20), 240))
        except ValueError:
            link.prep_time_minutes = 20
        creds = link.get_credentials()
        token = request.POST.get("api_token", "").strip()
        if token:
            creds["api_token"] = token
        if request.POST.get("clear_token"):
            creds.pop("api_token", None)
        link.set_credentials(creds)
        if not link.menu_token:
            link.menu_token = secrets.token_urlsafe(24)
        link.save()
        _audit(
            request,
            f"{link.get_platform_display()} settings updated",
            {"enabled": link.is_enabled, "store": link.store_external_id},
        )
        messages.success(request, f"{link.get_platform_display()} saved.")
        return self._back()

    def rotate_webhook_view(self, request, code):
        link = self._link(request, code)
        link.webhook_token = secrets.token_urlsafe(32)
        link.save(update_fields=["webhook_token", "updated_at"])
        request.session[f"delivery:wt:{link.pk}"] = link.webhook_token
        messages.success(request, "Webhook token rotated. Copy it now; it is shown once.")
        return self._back()

    def rotate_menu_view(self, request, code):
        link = self._link(request, code)
        link.menu_token = secrets.token_urlsafe(24)
        link.save(update_fields=["menu_token", "updated_at"])
        messages.success(request, "Menu feed URL rotated. Push the menu again so the platform learns the new URL.")
        return self._back()

    def push_menu_view(self, request, code):
        link = self._link(request, code)
        if code not in IMPLEMENTED:
            messages.error(request, "This platform's API integration is not available yet.")
            return self._back()
        if not link.menu_token:
            link.menu_token = secrets.token_urlsafe(24)
            link.save(update_fields=["menu_token", "updated_at"])
        services.start_menu_sync(link, by=request.user)
        messages.success(request, "Menu push queued.")
        return self._back()


def _audit(request, description, changes):
    try:
        from apps.audit.services import log_action

        log_action(
            "settings_update", request=request, restaurant=request.restaurant, description=description, changes=changes
        )
    except Exception:  # pragma: no cover
        pass


def register_delivery_admin(site):
    site.register(DeliveryPlatformsPage, DeliveryPlatformsTenantAdmin)
