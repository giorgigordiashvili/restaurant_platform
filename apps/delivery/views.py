"""Dashboard (POS / admin SPA) API: platform status, pause / resume the store, menu pushes."""

from __future__ import annotations

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired
from apps.delivery import menu_import, services
from apps.delivery.models import MenuImport, RestaurantDeliveryPlatform
from apps.delivery.serializers import (
    MenuImportApplySerializer,
    MenuImportRequestSerializer,
    MenuImportSerializer,
    MenuSyncRequestSerializer,
    MenuSyncSerializer,
    PauseSerializer,
    PlatformStatusSerializer,
    StoreStatusSerializer,
)
from apps.orders.models import Order

TAG = "Dashboard - Delivery"
PERMS = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("delivery")]


def _row(link, counts) -> dict:
    paused = link.store_paused_until
    if paused and paused <= timezone.now():
        paused = None
    c = counts.get(link.platform, {})
    return {
        "platform": link.platform,
        "label": link.get_platform_display(),
        "implemented": link.platform in services.IMPLEMENTED,
        "is_enabled": link.is_enabled,
        "configured": services.is_configured(link),
        "store_external_id": link.store_external_id,
        "auto_accept": link.auto_accept,
        "prep_time_minutes": link.prep_time_minutes,
        "sandbox": link.sandbox,
        "online": paused is None,
        "paused_until": paused,
        "last_menu_sync_at": link.last_menu_sync_at,
        "last_menu_sync_status": link.last_menu_sync_status,
        "orders_today": c.get("today", 0),
        "awaiting_accept": c.get("pending", 0),
    }


def _counts(restaurant) -> dict:
    tz = timezone.get_current_timezone()
    start = timezone.now().astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (
        Order.objects.filter(restaurant=restaurant, source__in=services.PLATFORM_SOURCES)
        .values("source")
        .annotate(today=Count("id", filter=Q(created_at__gte=start)), pending=Count("id", filter=Q(status="pending")))
    )
    return {r["source"]: r for r in rows}


class _LinkView(APIView):
    permission_classes = PERMS

    def _link(self, request, code):
        if code not in RestaurantDeliveryPlatform.MARKETPLACES:
            return None
        link, _ = RestaurantDeliveryPlatform.objects.get_or_create(
            restaurant=request.restaurant, platform=code, defaults={"is_enabled": False}
        )
        return link

    def _not_found(self):
        return Response({"success": False, "error": {"code": "unknown_platform"}}, status=status.HTTP_404_NOT_FOUND)


@extend_schema(tags=[TAG], responses=PlatformStatusSerializer(many=True))
class PlatformListView(APIView):
    permission_classes = PERMS
    required_permission = ("orders", "read")

    @require_restaurant
    def get(self, request):
        links = []
        for code in RestaurantDeliveryPlatform.MARKETPLACES:
            link, _ = RestaurantDeliveryPlatform.objects.get_or_create(
                restaurant=request.restaurant, platform=code, defaults={"is_enabled": False}
            )
            links.append(link)
        counts = _counts(request.restaurant)
        return Response(PlatformStatusSerializer([_row(l, counts) for l in links], many=True).data)


@extend_schema(tags=[TAG], responses=StoreStatusSerializer)
class StoreStatusView(_LinkView):
    required_permission = ("orders", "read")

    @require_restaurant
    def get(self, request, code):
        link = self._link(request, code)
        if link is None:
            return self._not_found()
        return Response(StoreStatusSerializer(services.store_status(link)).data)


@extend_schema(tags=[TAG], request=PauseSerializer, responses=StoreStatusSerializer)
class PauseStoreView(_LinkView):
    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request, code):
        link = self._link(request, code)
        if link is None:
            return self._not_found()
        ser = PauseSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            services.pause_store(link, ser.validated_data["minutes"], by=request.user)
        except services.DeliveryError as exc:
            return Response(
                {"success": False, "error": {"code": exc.code, "message": exc.message}},
                status=status.HTTP_502_BAD_GATEWAY if exc.code == "platform_error" else status.HTTP_400_BAD_REQUEST,
            )
        return Response(StoreStatusSerializer(services.store_status(link)).data)


@extend_schema(tags=[TAG], request=None, responses=StoreStatusSerializer)
class ResumeStoreView(_LinkView):
    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request, code):
        link = self._link(request, code)
        if link is None:
            return self._not_found()
        try:
            services.resume_store(link, by=request.user)
        except services.DeliveryError as exc:
            return Response(
                {"success": False, "error": {"code": exc.code, "message": exc.message}},
                status=status.HTTP_502_BAD_GATEWAY if exc.code == "platform_error" else status.HTTP_400_BAD_REQUEST,
            )
        return Response(StoreStatusSerializer(services.store_status(link)).data)


@extend_schema(tags=[TAG], request=MenuSyncRequestSerializer, responses=MenuSyncSerializer)
class MenuSyncView(_LinkView):
    required_permission = ("settings", "update")

    @require_restaurant
    def post(self, request, code):
        link = self._link(request, code)
        if link is None:
            return self._not_found()
        if code not in services.IMPLEMENTED:
            return Response(
                {"success": False, "error": {"code": "not_implemented"}}, status=status.HTTP_400_BAD_REQUEST
            )
        ser = MenuSyncRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        sync = services.start_menu_sync(link, by=request.user, kind=ser.validated_data["kind"])
        sync.refresh_from_db()
        return Response(
            MenuSyncSerializer(
                {
                    "id": sync.pk,
                    "status": sync.status,
                    "kind": sync.request.get("kind", "full"),
                    "product_count": sync.product_count,
                    "error": sync.error,
                    "created_at": sync.created_at,
                    "finished_at": sync.finished_at,
                }
            ).data,
            status=status.HTTP_202_ACCEPTED,
        )


def _import_payload(row):
    return MenuImportSerializer(
        {
            "id": row.pk,
            "source": row.source,
            "status": row.status,
            "preview": row.preview,
            "stats": row.stats,
            "error": row.error,
            "created_at": row.created_at,
            "applied_at": row.applied_at,
        }
    ).data


def _import_error(exc):
    code = status.HTTP_502_BAD_GATEWAY if exc.code == "platform_error" else status.HTTP_400_BAD_REQUEST
    return Response({"success": False, "error": {"code": exc.code, "message": exc.message}}, status=code)


@extend_schema(tags=[TAG], request=MenuImportRequestSerializer, responses=MenuImportSerializer)
class MenuImportPreviewView(_LinkView):
    """Fetch the platform's menu (Wolt) and return a preview; nothing is written to the menu yet."""

    required_permission = ("menu", "create")

    @require_restaurant
    def post(self, request, code):
        link = self._link(request, code)
        if link is None:
            return self._not_found()
        ser = MenuImportRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            row = menu_import.fetch_preview(link, by=request.user, price_units=ser.validated_data["price_units"])
        except menu_import.ImportError_ as exc:
            return _import_error(exc)
        return Response(_import_payload(row))


@extend_schema(tags=[TAG], responses=MenuImportSerializer)
class MenuImportDetailView(APIView):
    permission_classes = PERMS
    required_permission = ("menu", "read")

    @require_restaurant
    def get(self, request, import_id):
        row = get_object_or_404(MenuImport, pk=import_id, restaurant=request.restaurant)
        return Response(_import_payload(row))


@extend_schema(tags=[TAG], request=MenuImportApplySerializer, responses=MenuImportSerializer)
class MenuImportApplyView(APIView):
    permission_classes = PERMS
    required_permission = ("menu", "create")

    @require_restaurant
    def post(self, request, import_id):
        row = get_object_or_404(MenuImport, pk=import_id, restaurant=request.restaurant)
        ser = MenuImportApplySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            menu_import.apply(row, by=request.user, **ser.validated_data)
        except menu_import.ImportError_ as exc:
            return _import_error(exc)
        row.refresh_from_db()
        return Response(_import_payload(row))
