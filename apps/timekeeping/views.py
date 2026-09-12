"""Dashboard (POS) API: clock in / out, who's in, my entries and my shifts, the week's rota."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import IsTenantStaff, ModuleRequired, staff_can
from apps.timekeeping import services
from apps.timekeeping.models import TimeEntry
from apps.timekeeping.serializers import (
    ClockActionSerializer,
    ClockStatusSerializer,
    RotaShiftSerializer,
    TimeEntrySerializer,
)

TAG = "Dashboard - Timekeeping"
PERMS = [IsAuthenticated, IsTenantStaff, ModuleRequired("timekeeping")]


def _member_or_403(request):
    member = services.member_for(request.user, request.restaurant)
    if member is None and request.restaurant.owner_id == request.user.pk:
        return None
    return member


def _status(request, member):
    entry = services.open_entry(member) if member else None
    return {
        "clocked_in": entry is not None,
        "entry": entry,
        "today_minutes": services.today_minutes(member) if member else 0,
        "manager": staff_can(request, "timekeeping", "update"),
    }


@extend_schema(tags=[TAG], request=ClockActionSerializer, responses=ClockStatusSerializer)
class ClockView(APIView):
    permission_classes = PERMS

    @require_restaurant
    def get(self, request):
        member = _member_or_403(request)
        return Response(ClockStatusSerializer(_status(request, member)).data)

    @require_restaurant
    def post(self, request):
        member = _member_or_403(request)
        if member is None:
            return Response(
                {
                    "success": False,
                    "error": {"code": "no_membership", "message": "Owners without a staff record cannot clock in."},
                },
                status=400,
            )
        ser = ClockActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            if ser.validated_data["action"] == "in":
                services.clock_in(member, by=request.user, note=ser.validated_data.get("note", ""))
            else:
                services.clock_out(
                    member,
                    by=request.user,
                    break_minutes=ser.validated_data.get("break_minutes", 0),
                    note=ser.validated_data.get("note", ""),
                )
        except services.TimekeepingError as exc:
            return Response(
                {"success": False, "error": {"code": exc.code, "message": exc.message}}, status=status.HTTP_409_CONFLICT
            )
        return Response(ClockStatusSerializer(_status(request, member)).data)


@extend_schema(tags=[TAG], responses=TimeEntrySerializer(many=True))
class WhosInView(APIView):
    permission_classes = PERMS

    @require_restaurant
    def get(self, request):
        if not staff_can(request, "timekeeping", "read"):
            return Response({"detail": "timekeeping:read required"}, status=403)
        return Response(TimeEntrySerializer(services.whos_in(request.restaurant), many=True).data)


@extend_schema(tags=[TAG], responses=TimeEntrySerializer(many=True))
class EntriesView(APIView):
    """?from=&to= (dates). Managers (timekeeping:update) see everyone; others their own."""

    permission_classes = PERMS

    @require_restaurant
    def get(self, request):
        qs = TimeEntry.objects.filter(restaurant=request.restaurant).select_related(
            "staff_member__user", "staff_member__role"
        )
        if not staff_can(request, "timekeeping", "update"):
            member = _member_or_403(request)
            qs = qs.filter(staff_member=member) if member else qs.none()
        today = timezone.localdate()
        try:
            start = datetime.strptime(request.query_params.get("from", ""), "%Y-%m-%d").date()
        except ValueError:
            start = today - timedelta(days=14)
        try:
            end = datetime.strptime(request.query_params.get("to", ""), "%Y-%m-%d").date()
        except ValueError:
            end = today
        qs = qs.filter(clock_in__date__gte=start, clock_in__date__lte=end).order_by("-clock_in")[:500]
        return Response(TimeEntrySerializer(qs, many=True).data)


@extend_schema(tags=[TAG], responses=RotaShiftSerializer(many=True))
class RotaView(APIView):
    """?week=YYYY-MM-DD (any day of the week). Published shifts only unless timekeeping:update."""

    permission_classes = PERMS

    @require_restaurant
    def get(self, request):
        try:
            day = datetime.strptime(request.query_params.get("week", ""), "%Y-%m-%d").date()
        except ValueError:
            day = timezone.localdate()
        monday = services.week_start(day)
        qs = services.week_shifts(request.restaurant, monday)
        if not staff_can(request, "timekeeping", "update"):
            qs = qs.filter(published=True)
        return Response(RotaShiftSerializer(qs, many=True).data)


@extend_schema(tags=[TAG], responses=RotaShiftSerializer(many=True))
class MyShiftsView(APIView):
    permission_classes = PERMS

    @require_restaurant
    def get(self, request):
        member = _member_or_403(request)
        rows = services.my_upcoming(member) if member else []
        return Response(RotaShiftSerializer(rows, many=True).data)
