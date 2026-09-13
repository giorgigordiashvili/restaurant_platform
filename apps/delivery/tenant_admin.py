"""Delivery platforms page: one card per platform with credentials, webhook / menu URLs, tokens, menu push, store pause, recent events."""

from __future__ import annotations

import secrets

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantModelAdmin, has_resource_permission
from apps.delivery import menu_import, services
from apps.delivery.models import DeliveryPlatformsPage, MenuImport, RestaurantDeliveryPlatform

IMPLEMENTED = set(services.IMPLEMENTED)

# Per-platform credential fields (stored encrypted on the link). ``secret`` fields render as password inputs.
CREDENTIALS = {
    "glovo": [
        ("api_token", "API token", True, "Issued by Glovo for the integration."),
        ("auth_scheme", "Authorization scheme", False, "Leave empty for a raw token; 'Bearer' if Glovo asks."),
    ],
    "wolt": [
        ("client_id", "OAuth client id", False, ""),
        ("client_secret", "OAuth client secret", True, ""),
        (
            "authorization_code",
            "One-time authorization code",
            True,
            "From Wolt onboarding; exchanged for a refresh token on first use.",
        ),
        ("refresh_token", "Refresh token", True, "Rotates automatically after the first call."),
        ("api_key", "Legacy API key (WOLT-API-KEY)", True, "Only for venues onboarded before OAuth."),
    ],
    "bolt_food": [],
}
STORE_LABELS = {"glovo": "Store id on Glovo", "wolt": "Venue id on Wolt", "bolt_food": "Provider id"}


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
        now = timezone.now()
        cards = []
        labels = dict(RestaurantDeliveryPlatform.PLATFORM_CHOICES)
        for code in RestaurantDeliveryPlatform.MARKETPLACES:
            label = labels[code]
            link, _created = RestaurantDeliveryPlatform.objects.get_or_create(
                restaurant=restaurant, platform=code, defaults={"is_enabled": False}
            )
            creds = link.get_credentials()
            n = "tenant_admin:delivery_deliveryplatformspage_"
            paused = link.store_paused_until if link.store_paused_until and link.store_paused_until > now else None
            cards.append(
                {
                    "link": link,
                    "code": code,
                    "label": label,
                    "implemented": code in IMPLEMENTED,
                    "configured": services.is_configured(link),
                    "store_label": STORE_LABELS.get(code, "Store id"),
                    "fields": [
                        {"name": name, "label": flabel, "secret": secret, "hint": hint, "is_set": bool(creds.get(name))}
                        for name, flabel, secret, hint in CREDENTIALS.get(code, [])
                    ],
                    "webhook_url": (
                        f"{base}{reverse('delivery:glovo-orders')}"
                        if code == "glovo"
                        else f"{base}{reverse('delivery:wolt-orders')}" if code == "wolt" else ""
                    ),
                    "cancel_url": (
                        f"{base}/api/v1/delivery/glovo/orders/{{order_id}}/cancel/" if code == "glovo" else ""
                    ),
                    "menu_url": services.menu_feed_url(link) if code == "glovo" and link.menu_token else "",
                    "paused_until": paused,
                    "last_sync": link.menu_syncs.first(),
                    "events": list(link.events.select_related("order")[:10]),
                    "webhook_token_once": request.session.pop(f"delivery:wt:{link.pk}", None),
                    "urls": {
                        "save": reverse(f"{n}save", args=[code]),
                        "rotate_webhook": reverse(f"{n}rotate_webhook", args=[code]),
                        "rotate_menu": reverse(f"{n}rotate_menu", args=[code]),
                        "push_menu": reverse(f"{n}push_menu", args=[code]),
                        "sync_updates": reverse(f"{n}sync_updates", args=[code]),
                        "pause": reverse(f"{n}pause", args=[code]),
                        "resume": reverse(f"{n}resume", args=[code]),
                        "check": reverse(f"{n}check", args=[code]),
                        "import_menu": reverse(f"{n}import_menu", args=[code]),
                    },
                    "can_import": code == "wolt",
                    "last_import": link.menu_imports.first(),
                }
            )
        return {
            "cards": cards,
            "can_manage": has_resource_permission(request, "settings", "update"),
            "guide_url": reverse("tenant_admin:delivery_deliveryplatformspage_guide"),
        }

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
            path("<slug:code>/sync-updates/", wrap(require_POST(self.sync_updates_view)), name=f"{n}_sync_updates"),
            path("<slug:code>/pause/", wrap(require_POST(self.pause_view)), name=f"{n}_pause"),
            path("<slug:code>/resume/", wrap(require_POST(self.resume_view)), name=f"{n}_resume"),
            path("<slug:code>/check/", wrap(require_POST(self.check_view)), name=f"{n}_check"),
            path("<slug:code>/import-menu/", wrap(require_POST(self.import_menu_view)), name=f"{n}_import_menu"),
            path("imports/<uuid:import_id>/", wrap(self.import_preview_view), name=f"{n}_import_preview"),
            path(
                "imports/<uuid:import_id>/apply/", wrap(require_POST(self.import_apply_view)), name=f"{n}_import_apply"
            ),
            path("guide/", wrap(self.guide_view), name=f"{n}_guide"),
        ]
        return custom + super().get_urls()

    def _link(self, request, code, *, implemented=False):
        if (
            not getattr(request, "restaurant", None)
            or code not in RestaurantDeliveryPlatform.MARKETPLACES
            or not has_resource_permission(request, "settings", "update")
        ):
            raise PermissionDenied
        if implemented and code not in IMPLEMENTED:
            raise PermissionDenied
        link, _created = RestaurantDeliveryPlatform.objects.get_or_create(
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
        creds = {} if request.POST.get("clear_credentials") else link.get_credentials()
        if not request.POST.get("clear_credentials"):
            changed = False
            for name, _label, _secret, _hint in CREDENTIALS.get(code, []):
                value = request.POST.get(name, "").strip()
                if value:
                    creds[name] = value
                    changed = True
            if request.POST.get("clear_token"):  # legacy checkbox name
                creds.pop("api_token", None)
            if changed:  # new OAuth material invalidates the cached access token
                creds.pop("access_token", None)
                creds.pop("access_expires_at", None)
        link.set_credentials(creds)
        if not link.menu_token:
            link.menu_token = secrets.token_urlsafe(24)
        link.save()
        if code == "wolt":
            from apps.delivery.wolt import auth

            auth.forget_token(link, persist=False)
        _audit(
            request,
            f"{link.get_platform_display()} settings updated",
            {"enabled": link.is_enabled, "store": link.store_external_id},
        )
        messages.success(request, _("{p0} saved.").format(p0=link.get_platform_display()))
        return self._back()

    def rotate_webhook_view(self, request, code):
        link = self._link(request, code)
        link.webhook_token = secrets.token_urlsafe(32)
        link.save(update_fields=["webhook_token", "updated_at"])
        request.session[f"delivery:wt:{link.pk}"] = link.webhook_token
        messages.success(request, _("Webhook token rotated. Copy it now; it is shown once."))
        return self._back()

    def rotate_menu_view(self, request, code):
        link = self._link(request, code)
        link.menu_token = secrets.token_urlsafe(24)
        link.save(update_fields=["menu_token", "updated_at"])
        messages.success(request, _("Menu feed URL rotated. Push the menu again so the platform learns the new URL."))
        return self._back()

    def push_menu_view(self, request, code):
        link = self._link(request, code)
        if code not in IMPLEMENTED:
            messages.error(request, _("This platform's API integration is not available yet."))
            return self._back()
        if not link.menu_token:
            link.menu_token = secrets.token_urlsafe(24)
            link.save(update_fields=["menu_token", "updated_at"])
        services.start_menu_sync(link, by=request.user)
        messages.success(request, _("Menu push queued."))
        return self._back()

    def sync_updates_view(self, request, code):
        link = self._link(request, code, implemented=True)
        services.start_menu_sync(link, by=request.user, kind="updates")
        messages.success(request, _("Prices and availability sync queued."))
        return self._back()

    def pause_view(self, request, code):
        link = self._link(request, code, implemented=True)
        try:
            minutes = int(request.POST.get("minutes") or 30)
        except ValueError:
            minutes = 30
        try:
            event = services.pause_store(link, minutes, by=request.user)
        except (services.DeliveryError, Exception) as exc:  # noqa: BLE001 - shown to the admin
            messages.error(request, _("Could not pause: {p0}").format(p0=exc))
            return self._back()
        _audit(request, f"{link.get_platform_display()} store paused", {"until": event.payload.get("until")})
        messages.success(request, _("{p0} paused for {p1} minutes.").format(p0=link.get_platform_display(), p1=minutes))
        return self._back()

    def resume_view(self, request, code):
        link = self._link(request, code, implemented=True)
        try:
            services.resume_store(link, by=request.user)
        except (services.DeliveryError, Exception) as exc:  # noqa: BLE001
            messages.error(request, _("Could not resume: {p0}").format(p0=exc))
            return self._back()
        _audit(request, f"{link.get_platform_display()} store resumed", {})
        messages.success(request, _("{p0} is taking orders again.").format(p0=link.get_platform_display()))
        return self._back()

    def check_view(self, request, code):
        """Live status from the platform (Wolt venue status; Glovo has no read endpoint -> our own record)."""
        link = self._link(request, code, implemented=True)
        try:
            status = services.store_status(link)
        except Exception as exc:  # noqa: BLE001
            messages.error(request, _("Status check failed: {p0}").format(p0=exc))
            return self._back()
        live = status.get("live") or {}
        if live.get("error"):
            messages.error(request, f"{link.get_platform_display()}: {live['error']}")
        elif live:
            messages.info(
                request,
                f"{link.get_platform_display()}: online={live.get('is_online')} open={live.get('is_open')} "
                f"last orders={live.get('last_orders')}",
            )
        else:
            messages.info(
                request,
                _("{p0}: ").format(p0=link.get_platform_display())
                + ("paused until " + status["paused_until"] if status["paused_until"] else "not paused by us"),
            )
        return self._back()

    # -- menu import ----------------------------------------------------------

    def import_menu_view(self, request, code):
        """Step 1: pull the menu from the platform and show a preview."""
        link = self._link(request, code, implemented=True)
        if not has_resource_permission(request, "menu", "create"):
            raise PermissionDenied
        units = request.POST.get("price_units") or "auto"
        try:
            row = menu_import.fetch_preview(
                link, by=request.user, price_units=units if units in ("auto", "major", "minor") else "auto"
            )
        except menu_import.ImportError_ as exc:
            messages.error(request, _("Could not fetch the menu: {p0}").format(p0=exc.message))
            return self._back()
        return redirect("tenant_admin:delivery_deliveryplatformspage_import_preview", import_id=row.pk)

    def _import(self, request, import_id):
        if not getattr(request, "restaurant", None) or not has_resource_permission(request, "menu", "create"):
            raise PermissionDenied
        return get_object_or_404(MenuImport, pk=import_id, restaurant=request.restaurant)

    def import_preview_view(self, request, import_id):
        row = self._import(request, import_id)
        context = {
            **self.admin_site.each_context(request),
            "title": f"Import menu from {row.get_source_display()}",
            "row": row,
            "preview": row.preview,
            "back_url": reverse("tenant_admin:delivery_deliveryplatformspage_changelist"),
            "apply_url": reverse("tenant_admin:delivery_deliveryplatformspage_import_apply", args=[row.pk]),
            "refetch_url": reverse("tenant_admin:delivery_deliveryplatformspage_import_menu", args=[row.source]),
            "menu_url": reverse("tenant_admin:menu_menuitem_changelist"),
        }
        request.current_app = self.admin_site.name
        return render(request, "admin/delivery/deliveryplatformspage/menu_import.html", context)

    def import_apply_view(self, request, import_id):
        """Step 2: create the categories / dishes / options that are missing; images download in the background."""
        row = self._import(request, import_id)
        try:
            stats = menu_import.apply(
                row,
                by=request.user,
                update_prices=bool(request.POST.get("update_prices")),
                download_images=bool(request.POST.get("download_images", "1")),
            )
        except menu_import.ImportError_ as exc:
            messages.error(request, exc.message)
            return redirect("tenant_admin:delivery_deliveryplatformspage_import_preview", import_id=row.pk)
        messages.success(
            request,
            f"Imported: {stats['categories_created']} categories, {stats['items_created']} dishes, "
            f"{stats['groups_created']} option groups; {stats['items_skipped']} already existed"
            + (f", {stats['items_updated']} prices updated" if stats["items_updated"] else "")
            + (f". {stats['images_queued']} images are downloading." if stats["images_queued"] else "."),
        )
        return redirect("tenant_admin:menu_menuitem_changelist")

    def guide_view(self, request):
        """Step-by-step onboarding for Wolt / Glovo with the guide videos (set the URLs in settings)."""
        if not getattr(request, "restaurant", None) or not self.has_view_permission(request):
            raise PermissionDenied
        base = getattr(settings, "PUBLIC_API_BASE_URL", "").rstrip("/")
        context = {
            **self.admin_site.each_context(request),
            "title": "Connecting Wolt and Glovo",
            "videos": {
                "wolt": getattr(settings, "DELIVERY_GUIDE_VIDEO_WOLT", ""),
                "glovo": getattr(settings, "DELIVERY_GUIDE_VIDEO_GLOVO", ""),
            },
            "wolt_webhook": f"{base}{reverse('delivery:wolt-orders')}",
            "glovo_webhook": f"{base}{reverse('delivery:glovo-orders')}",
            "platforms_url": reverse("tenant_admin:delivery_deliveryplatformspage_changelist"),
        }
        request.current_app = self.admin_site.name
        return render(request, "admin/delivery/deliveryplatformspage/guide.html", context)


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
