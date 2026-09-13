"""Dashboard API for the POS (queue, add, notify, seat …) and the public join / status endpoints."""

from __future__ import annotations

from datetime import date

from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiParameter, extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired, staff_can
from apps.reservations.models import Reservation
from apps.tables.models import Table
from apps.tenants.models import Restaurant
from apps.waitlist import services
from apps.waitlist.models import WaitlistEntry
from apps.waitlist.serializers import (
    AddEntrySerializer,
    JoinSerializer,
    PublicStatusSerializer,
    SeatSerializer,
    SummarySerializer,
    UpdateEntrySerializer,
    WaitlistEntrySerializer,
    WaitlistSettingsSerializer,
)

TAG = "Dashboard - Waitlist"
PUBLIC_TAG = "Waitlist"
PERMS = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("waitlist")]


def _err(exc: services.WaitlistError, http=status.HTTP_409_CONFLICT):
    return Response({"success": False, "error": {"code": exc.code, "message": exc.message, **exc.extra}}, status=http)


@extend_schema(tags=[TAG], responses=SummarySerializer)
class SummaryView(APIView):
    permission_classes = PERMS
    required_permission = ("reservations", "read")

    @require_restaurant
    def get(self, request):
        return Response(SummarySerializer(services.summary(request.restaurant)).data)


@extend_schema(tags=[TAG], request=WaitlistSettingsSerializer, responses=WaitlistSettingsSerializer)
class SettingsView(APIView):
    permission_classes = PERMS
    required_permission = ("reservations", "read")

    @require_restaurant
    def get(self, request):
        return Response(WaitlistSettingsSerializer(services.settings_for(request.restaurant)).data)

    @require_restaurant
    def patch(self, request):
        if not staff_can(request, "settings", "update"):
            return Response({"detail": "settings:update needed."}, status=status.HTTP_403_FORBIDDEN)
        s = WaitlistSettingsSerializer(services.settings_for(request.restaurant), data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data)


@extend_schema(tags=[TAG])
class EntryListView(APIView):
    permission_classes = PERMS
    required_permission = ("reservations", "read")

    @extend_schema(
        parameters=[OpenApiParameter("date", str), OpenApiParameter("all", bool)],
        responses=WaitlistEntrySerializer(many=True),
    )
    @require_restaurant
    def get(self, request):
        raw = request.query_params.get("date")
        try:
            day = date.fromisoformat(raw) if raw else None
        except ValueError:
            day = None
        qs = services.queue(request.restaurant, day)
        if request.query_params.get("all") not in ("1", "true"):
            qs = qs.filter(status__in=WaitlistEntry.OPEN)
        return Response(WaitlistEntrySerializer(qs, many=True).data)

    @extend_schema(request=AddEntrySerializer, responses=WaitlistEntrySerializer)
    @require_restaurant
    def post(self, request):
        if not staff_can(request, "reservations", "create") and not staff_can(request, "tables", "update"):
            return Response({"detail": "reservations:create needed."}, status=status.HTTP_403_FORBIDDEN)
        s = AddEntrySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        try:
            entry = services.add_entry(
                request.restaurant,
                name=v["name"],
                phone=v.get("phone", ""),
                party_size=v["party_size"],
                quoted=v.get("quoted_minutes"),
                notes=v.get("notes", ""),
                by=request.user,
            )
        except services.WaitlistError as exc:
            return _err(exc, status.HTTP_400_BAD_REQUEST)
        return Response(WaitlistEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


class _EntryView(APIView):
    permission_classes = PERMS
    required_permission = ("reservations", "update")

    def _entry(self, request, entry_id) -> WaitlistEntry:
        return get_object_or_404(
            WaitlistEntry.objects.select_related("restaurant", "table", "reservation"),
            pk=entry_id,
            restaurant=request.restaurant,
        )


@extend_schema(tags=[TAG], request=UpdateEntrySerializer, responses=WaitlistEntrySerializer)
class EntryDetailView(_EntryView):
    @require_restaurant
    def patch(self, request, entry_id):
        entry = self._entry(request, entry_id)
        s = UpdateEntrySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = dict(s.validated_data)
        position = v.pop("position", None)
        services.update_entry(entry, by=request.user, **v)
        if position:
            services.reorder(entry, position)
            entry.refresh_from_db()
        return Response(WaitlistEntrySerializer(entry).data)


@extend_schema(tags=[TAG], request=None, responses=WaitlistEntrySerializer)
class EntryNotifyView(_EntryView):
    @require_restaurant
    def post(self, request, entry_id):
        try:
            entry = services.notify_ready(self._entry(request, entry_id), by=request.user)
        except services.WaitlistError as exc:
            return _err(exc)
        return Response(WaitlistEntrySerializer(entry).data)


@extend_schema(tags=[TAG], request=SeatSerializer, responses=WaitlistEntrySerializer)
class EntrySeatView(_EntryView):
    @require_restaurant
    def post(self, request, entry_id):
        entry = self._entry(request, entry_id)
        s = SeatSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        table = get_object_or_404(Table, pk=s.validated_data["table_id"], restaurant=request.restaurant)
        try:
            services.seat(entry, table, by=request.user)
        except services.WaitlistError as exc:
            return _err(exc)
        entry.refresh_from_db()
        return Response(WaitlistEntrySerializer(entry).data)


class _MarkView(_EntryView):
    target = ""

    @extend_schema(tags=[TAG], request=None, responses=WaitlistEntrySerializer)
    @require_restaurant
    def post(self, request, entry_id):
        try:
            entry = services.mark(self._entry(request, entry_id), self.target, by=request.user)
        except services.WaitlistError as exc:
            return _err(exc)
        return Response(WaitlistEntrySerializer(entry).data)


class EntryLeftView(_MarkView):
    target = "left"


class EntryNoShowView(_MarkView):
    target = "no_show"


class EntryCancelView(_MarkView):
    target = "cancelled"


@extend_schema(tags=[TAG], parameters=[OpenApiParameter("party_size", int)], responses={200: dict})
class EstimateView(APIView):
    permission_classes = PERMS
    required_permission = ("reservations", "read")

    @require_restaurant
    def get(self, request):
        try:
            size = max(int(request.query_params.get("party_size", 2)), 1)
        except ValueError:
            size = 2
        return Response({"party_size": size, "minutes": services.estimate_wait(request.restaurant, size)})


@extend_schema(tags=[TAG], request=None, responses=WaitlistEntrySerializer)
class ReservationToWaitlistView(APIView):
    permission_classes = PERMS
    required_permission = ("reservations", "update")

    @require_restaurant
    def post(self, request, reservation_id):
        reservation = get_object_or_404(Reservation, pk=reservation_id, restaurant=request.restaurant)
        try:
            entry = services.from_reservation(reservation, by=request.user)
        except services.WaitlistError as exc:
            return _err(exc)
        return Response(WaitlistEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


# ── public ─────────────────────────────────────────────────────────────────


class JoinThrottle(AnonRateThrottle):
    scope = "waitlist_join"
    rate = "20/min"


def _public_status(entry: WaitlistEntry) -> dict:
    ahead = (
        services.open_queue(entry.restaurant, entry.date).filter(position__lt=entry.position).count()
        if entry.is_open
        else 0
    )
    return {
        "name": entry.name,
        "party_size": entry.party_size,
        "status": entry.status,
        "position": entry.position if entry.is_open else 0,
        "ahead": ahead,
        "quoted_minutes": entry.quoted_minutes,
        "estimated_ready_at": entry.estimated_ready_at,
        "restaurant": entry.restaurant.name,
        "restaurant_slug": entry.restaurant.slug,
    }


@extend_schema(tags=[PUBLIC_TAG], responses={200: dict})
class PublicJoinInfoView(APIView):
    """The QR at the door: is the list open, how long is it, what is the max party."""

    permission_classes = [AllowAny]
    throttle_classes = [JoinThrottle]

    def get(self, request, slug, token):
        restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
        cfg = services.settings_for(restaurant)
        if not services.enabled(restaurant) or cfg.join_token != token:
            return Response({"success": False, "error": {"code": "not_found"}}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "success": True,
                "data": {
                    "restaurant": restaurant.name,
                    "open": cfg.allow_self_join,
                    "waiting": services.open_queue(restaurant).count(),
                    "max_party_size": cfg.max_party_size,
                    "estimate": services.estimate_wait(restaurant, 2),
                },
            }
        )

    @extend_schema(request=JoinSerializer, responses={201: dict})
    def post(self, request, slug, token):
        restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
        cfg = services.settings_for(restaurant)
        if not services.enabled(restaurant) or cfg.join_token != token:
            return Response({"success": False, "error": {"code": "not_found"}}, status=status.HTTP_404_NOT_FOUND)
        s = JoinSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        try:
            entry = services.add_entry(
                restaurant, name=v["name"], phone=v["phone"], party_size=v["party_size"], source="self"
            )
        except services.WaitlistError as exc:
            return _err(exc, status.HTTP_400_BAD_REQUEST)
        return Response({"success": True, "data": {"token": entry.token, **_public_status(entry)}}, status=201)


@extend_schema(tags=[PUBLIC_TAG], responses=PublicStatusSerializer)
class PublicStatusView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [JoinThrottle]

    def get(self, request, token):
        entry = get_object_or_404(WaitlistEntry.objects.select_related("restaurant"), token=token)
        return Response({"success": True, "data": _public_status(entry)})

    @extend_schema(request=None, responses={200: dict})
    def delete(self, request, token):
        entry = get_object_or_404(WaitlistEntry.objects.select_related("restaurant"), token=token)
        if entry.is_open:
            services.mark(entry, "left")
        return Response({"success": True, "data": _public_status(entry)})
