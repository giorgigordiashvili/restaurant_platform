"""Dashboard (POS) API: my notifications, unread badge, devices, preferences."""

from __future__ import annotations

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import IsTenantStaff, ModuleRequired, staff_can
from apps.notifications import services
from apps.notifications.models import Device, Notification
from apps.notifications.serializers import (
    DeviceRegisterSerializer,
    DeviceSerializer,
    MarkReadSerializer,
    NotificationSerializer,
    OutboundMessageSerializer,
    PrefsSerializer,
    TestMessageSerializer,
    UnreadCountSerializer,
)

TAG = "Dashboard - Notifications"
PERMS = [IsAuthenticated, IsTenantStaff, ModuleRequired("notifications")]


@extend_schema(tags=[TAG])
class NotificationListView(generics.ListAPIView):
    permission_classes = PERMS
    serializer_class = NotificationSerializer

    @require_restaurant
    def get_queryset(self):
        qs = Notification.objects.filter(restaurant=self.request.restaurant, user=self.request.user)
        if self.request.query_params.get("unread") in ("1", "true"):
            qs = qs.filter(read_at__isnull=True)
        return qs


@extend_schema(tags=[TAG], responses=UnreadCountSerializer)
class UnreadCountView(APIView):
    permission_classes = PERMS

    @require_restaurant
    def get(self, request):
        qs = Notification.objects.filter(restaurant=request.restaurant, user=request.user, read_at__isnull=True)
        latest = qs.order_by("-created_at").values_list("pk", flat=True).first()
        return Response(UnreadCountSerializer({"unread": qs.count(), "latest_id": latest}).data)


@extend_schema(tags=[TAG], request=MarkReadSerializer, responses=UnreadCountSerializer)
class MarkReadView(APIView):
    """POST {ids: [...]} marks those; an empty body marks everything read."""

    permission_classes = PERMS

    @require_restaurant
    def post(self, request):
        ser = MarkReadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ids = ser.validated_data.get("ids")
        services.mark_read(request.restaurant, request.user, ids if ids else None)
        unread = services.unread_count(request.restaurant, request.user)
        return Response(UnreadCountSerializer({"unread": unread, "latest_id": None}).data)


@extend_schema(tags=[TAG], request=DeviceRegisterSerializer, responses=DeviceSerializer)
class DeviceView(APIView):
    permission_classes = PERMS

    @require_restaurant
    def get(self, request):
        rows = Device.objects.filter(user=request.user, is_active=True)
        return Response(DeviceSerializer(rows, many=True).data)

    @require_restaurant
    def post(self, request):
        ser = DeviceRegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        device = services.register_device(request.user, request.restaurant, **ser.validated_data)
        return Response(DeviceSerializer(device).data, status=status.HTTP_201_CREATED)

    @require_restaurant
    def delete(self, request):
        token = request.data.get("token") or request.query_params.get("token")
        n = Device.objects.filter(user=request.user, token=token).update(is_active=False) if token else 0
        return Response({"removed": n})


@extend_schema(tags=[TAG], request=PrefsSerializer, responses=PrefsSerializer)
class PrefsView(APIView):
    permission_classes = PERMS

    @require_restaurant
    def get(self, request):
        return Response(PrefsSerializer(services.prefs_for(request.restaurant, request.user)).data)

    @require_restaurant
    def put(self, request):
        prefs = services.prefs_for(request.restaurant, request.user)
        ser = PrefsSerializer(prefs, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(PrefsSerializer(prefs).data)


@extend_schema(tags=[TAG], request=None, responses=NotificationSerializer(many=True))
class TestPushView(APIView):
    """Send a test notification to myself (checks the device registration end to end)."""

    permission_classes = PERMS

    @require_restaurant
    def post(self, request):
        rows = services.notify(
            request.restaurant,
            "order.new",
            title="Test notification",
            body="If you can read this on your phone, push works.",
            data={"kind": "test"},
            users=[request.user],
        )
        return Response(NotificationSerializer(rows, many=True).data)


@extend_schema(tags=[TAG], request=TestMessageSerializer, responses=OutboundMessageSerializer)
class TestMessageView(APIView):
    """Managers: send a test SMS / email through the configured provider."""

    permission_classes = PERMS

    @require_restaurant
    def post(self, request):
        if not staff_can(request, "settings", "update"):
            return Response({"detail": "settings:update required"}, status=status.HTTP_403_FORBIDDEN)
        ser = TestMessageSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        msg = services.send_message(
            request.restaurant,
            ser.validated_data["channel"],
            ser.validated_data["to"],
            f"Test message from {request.restaurant.name} (AiMenu).",
            subject="AiMenu test",
            kind="test",
            by=request.user,
            force=True,
        )
        msg.refresh_from_db()
        return Response(OutboundMessageSerializer(msg).data)
