"""
Views for reservations.
"""

from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.middleware.tenant import get_current_restaurant, require_restaurant
from apps.core.permissions import IsTenantStaff

from .models import (
    Reservation,
    ReservationBlockedTime,
    ReservationSettings,
)
from .serializers import (
    AvailabilityRequestSerializer,
    AvailableSlotSerializer,
    ReservationBlockedTimeSerializer,
    ReservationCancelSerializer,
    ReservationCreateSerializer,
    ReservationDashboardCreateSerializer,
    ReservationDetailSerializer,
    ReservationListSerializer,
    ReservationSettingsAdminSerializer,
    ReservationSettingsSerializer,
    ReservationStatusUpdateSerializer,
    ReservationUpdateSerializer,
    TableAssignmentSerializer,
)


# ============== Public Views ==============


class PublicReservationSettingsView(APIView):
    """Get public reservation settings for a restaurant."""

    permission_classes = [AllowAny]

    @require_restaurant
    def get(self, request):
        restaurant = get_current_restaurant(request)
        try:
            settings = restaurant.reservation_settings
            serializer = ReservationSettingsSerializer(settings)
            return Response(serializer.data)
        except ReservationSettings.DoesNotExist:
            return Response(
                {
                    "accepts_reservations": True,
                    "min_party_size": 1,
                    "max_party_size": 20,
                    "advance_booking_days": 30,
                    "min_advance_hours": 2,
                    "slot_interval_minutes": 30,
                    "cancellation_deadline_hours": 24,
                }
            )


class PublicAvailabilityView(APIView):
    """Check available time slots for a given date and party size."""

    permission_classes = [AllowAny]

    @require_restaurant
    def get(self, request):
        restaurant = get_current_restaurant(request)
        serializer = AvailabilityRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)

        date = serializer.validated_data["date"]
        party_size = serializer.validated_data["party_size"]

        # Get restaurant settings
        try:
            settings = restaurant.reservation_settings
            if not settings.accepts_reservations:
                return Response(
                    {"error": "This restaurant does not accept online reservations."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            slot_interval = settings.slot_interval_minutes
        except ReservationSettings.DoesNotExist:
            slot_interval = 30

        # Get restaurant hours for the day
        day_of_week = date.weekday()
        try:
            hours = restaurant.hours.get(day_of_week=day_of_week)
            if hours.is_closed:
                return Response({"slots": [], "message": "Restaurant is closed on this day."})
            open_time = hours.open_time
            close_time = hours.close_time
        except Exception:
            open_time = time(11, 0)
            close_time = time(22, 0)

        # Get blocked times for this date
        blocked_times = ReservationBlockedTime.objects.filter(
            restaurant=restaurant,
            start_datetime__date__lte=date,
            end_datetime__date__gte=date,
        )

        # Get existing reservations for this date
        existing_reservations = Reservation.objects.filter(
            restaurant=restaurant,
            reservation_date=date,
            status__in=["pending", "confirmed", "waitlist"],
        )

        # Generate available slots
        slots = []
        current_time = open_time
        end_time = close_time

        # Adjust for today's date
        if date == timezone.now().date():
            try:
                settings = restaurant.reservation_settings
                min_advance = settings.min_advance_hours
            except ReservationSettings.DoesNotExist:
                min_advance = 2

            min_time = (timezone.now() + timedelta(hours=min_advance)).time()
            if min_time > current_time:
                # Round up to next slot
                minutes = min_time.hour * 60 + min_time.minute
                rounded_minutes = ((minutes + slot_interval - 1) // slot_interval) * slot_interval
                current_time = time(rounded_minutes // 60, rounded_minutes % 60)

        while current_time < end_time:
            slot_datetime = timezone.make_aware(datetime.combine(date, current_time))

            # Check if slot is blocked
            is_blocked = any(bt.start_datetime <= slot_datetime < bt.end_datetime for bt in blocked_times)

            if not is_blocked:
                # Count available tables
                tables_count = restaurant.tables.filter(
                    status="available",
                    capacity__gte=party_size,
                ).count()

                # Subtract booked tables
                for reservation in existing_reservations:
                    if reservation.table:
                        # Check for overlap
                        res_start = timezone.make_aware(datetime.combine(date, reservation.reservation_time))
                        res_end = res_start + reservation.duration
                        if res_start <= slot_datetime < res_end:
                            tables_count -= 1

                if tables_count > 0:
                    slots.append(
                        {
                            "date": date,
                            "time": current_time,
                            "available_tables": tables_count,
                        }
                    )

            # Move to next slot
            current_minutes = current_time.hour * 60 + current_time.minute + slot_interval
            if current_minutes >= 24 * 60:
                break
            current_time = time(current_minutes // 60, current_minutes % 60)

        result_serializer = AvailableSlotSerializer(slots, many=True)
        return Response({"slots": result_serializer.data})


class PublicReservationCreateView(generics.CreateAPIView):
    """Create a new reservation (public)."""

    serializer_class = ReservationCreateSerializer
    permission_classes = [AllowAny]

    @require_restaurant
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["restaurant"] = get_current_restaurant(self.request)
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reservation = serializer.save()
        return Response(
            {
                "success": True,
                "message": "Reservation created successfully.",
                "confirmation_code": reservation.confirmation_code,
                "reservation": ReservationDetailSerializer(reservation).data,
            },
            status=status.HTTP_201_CREATED,
        )


class PublicReservationLookupView(APIView):
    """Look up a reservation by confirmation code."""

    permission_classes = [AllowAny]

    @require_restaurant
    def get(self, request):
        restaurant = get_current_restaurant(request)
        confirmation_code = request.query_params.get("code")
        phone = request.query_params.get("phone")

        if not confirmation_code:
            return Response(
                {"error": "Confirmation code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            filters = {
                "restaurant": restaurant,
                "confirmation_code": confirmation_code.upper(),
            }
            if phone:
                filters["guest_phone__endswith"] = phone[-4:]

            reservation = Reservation.objects.get(**filters)
            serializer = ReservationDetailSerializer(reservation)
            return Response(serializer.data)
        except Reservation.DoesNotExist:
            return Response(
                {"error": "Reservation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )


class PublicReservationCancelView(APIView):
    """Cancel a reservation by confirmation code."""

    permission_classes = [AllowAny]

    @require_restaurant
    def post(self, request):
        restaurant = get_current_restaurant(request)
        confirmation_code = request.data.get("confirmation_code")

        if not confirmation_code:
            return Response(
                {"error": "Confirmation code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            reservation = Reservation.objects.get(
                restaurant=restaurant,
                confirmation_code=confirmation_code.upper(),
            )
        except Reservation.DoesNotExist:
            return Response(
                {"error": "Reservation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ReservationCancelSerializer(
            reservation,
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        reason = serializer.validated_data.get("reason", "")
        reservation.cancel(reason=reason)

        return Response(
            {
                "success": True,
                "message": "Reservation cancelled successfully.",
            }
        )


# ============== Customer Views (Authenticated) ==============


class CustomerReservationListView(generics.ListAPIView):
    """List customer's own reservations."""

    serializer_class = ReservationListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Reservation.objects.filter(customer=self.request.user).order_by("-reservation_date", "-reservation_time")


class CustomerReservationDetailView(generics.RetrieveAPIView):
    """Get customer's reservation detail."""

    serializer_class = ReservationDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Reservation.objects.filter(customer=self.request.user)


class CustomerReservationCancelView(APIView):
    """Cancel customer's own reservation."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            reservation = Reservation.objects.get(pk=pk, customer=request.user)
        except Reservation.DoesNotExist:
            return Response(
                {"error": "Reservation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ReservationCancelSerializer(
            reservation,
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        reason = serializer.validated_data.get("reason", "")
        reservation.cancel(cancelled_by=request.user, reason=reason)

        return Response(
            {
                "success": True,
                "message": "Reservation cancelled successfully.",
            }
        )


# ============== Dashboard Views ==============


class DashboardReservationSettingsView(generics.RetrieveUpdateAPIView):
    """Get or update reservation settings."""

    serializer_class = ReservationSettingsAdminSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff]

    @require_restaurant
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @require_restaurant
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)

    @require_restaurant
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)

    def get_object(self):
        restaurant = get_current_restaurant(self.request)
        settings, _ = ReservationSettings.objects.get_or_create(restaurant=restaurant)
        return settings


class DashboardReservationListView(generics.ListAPIView):
    """List reservations for dashboard."""

    serializer_class = ReservationListSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff]

    @require_restaurant
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        restaurant = get_current_restaurant(self.request)
        queryset = Reservation.objects.filter(restaurant=restaurant)

        # Filter by date
        date = self.request.query_params.get("date")
        if date:
            queryset = queryset.filter(reservation_date=date)

        # Filter by date range
        start_date = self.request.query_params.get("start_date")
        end_date = self.request.query_params.get("end_date")
        if start_date and end_date:
            queryset = queryset.filter(
                reservation_date__gte=start_date,
                reservation_date__lte=end_date,
            )

        # Filter by status
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Filter upcoming only
        upcoming = self.request.query_params.get("upcoming")
        if upcoming and upcoming.lower() == "true":
            queryset = queryset.filter(reservation_date__gte=timezone.now().date())

        # Search
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(confirmation_code__icontains=search)
                | Q(guest_name__icontains=search)
                | Q(guest_phone__icontains=search)
                | Q(guest_email__icontains=search)
            )

        return queryset.order_by("reservation_date", "reservation_time")


class DashboardReservationCreateView(generics.CreateAPIView):
    """Create a reservation from dashboard."""

    serializer_class = ReservationDashboardCreateSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff]

    @require_restaurant
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["restaurant"] = get_current_restaurant(self.request)
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reservation = serializer.save()
        return Response(
            {
                "success": True,
                "message": "Reservation created successfully.",
                "reservation": ReservationDetailSerializer(reservation).data,
            },
            status=status.HTTP_201_CREATED,
        )


class DashboardReservationDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Get, update, or delete a reservation."""

    serializer_class = ReservationDetailSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff]

    @require_restaurant
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @require_restaurant
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)

    @require_restaurant
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)

    @require_restaurant
    def delete(self, request, *args, **kwargs):
        return super().delete(request, *args, **kwargs)

    def get_queryset(self):
        restaurant = get_current_restaurant(self.request)
        return Reservation.objects.filter(restaurant=restaurant)

    def get_serializer_class(self):
        if self.request.method in ["PUT", "PATCH"]:
            return ReservationUpdateSerializer
        return ReservationDetailSerializer


class DashboardReservationStatusView(APIView):
    """Update reservation status."""

    permission_classes = [IsAuthenticated, IsTenantStaff]

    @require_restaurant
    def post(self, request, pk):
        restaurant = get_current_restaurant(request)
        try:
            reservation = Reservation.objects.get(pk=pk, restaurant=restaurant)
        except Reservation.DoesNotExist:
            return Response(
                {"error": "Reservation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ReservationStatusUpdateSerializer(
            reservation,
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        reservation = serializer.save()

        return Response(
            {
                "success": True,
                "message": f"Reservation status updated to {reservation.get_status_display()}.",
                "reservation": ReservationDetailSerializer(reservation).data,
            }
        )


class DashboardReservationAssignTableView(APIView):
    """Assign a table to a reservation."""

    permission_classes = [IsAuthenticated, IsTenantStaff]

    @require_restaurant
    def post(self, request, pk):
        restaurant = get_current_restaurant(request)
        try:
            reservation = Reservation.objects.get(pk=pk, restaurant=restaurant)
        except Reservation.DoesNotExist:
            return Response(
                {"error": "Reservation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = TableAssignmentSerializer(
            data=request.data,
            context={"request": request, "restaurant": restaurant},
        )
        serializer.is_valid(raise_exception=True)

        table = serializer.validated_data["table_id"]
        reservation.table = table
        reservation.save(update_fields=["table", "updated_at"])

        return Response(
            {
                "success": True,
                "message": f"Table {table.number} assigned to reservation.",
                "reservation": ReservationDetailSerializer(reservation).data,
            }
        )


class DashboardTodayReservationsView(generics.ListAPIView):
    """Get today's reservations."""

    serializer_class = ReservationListSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff]

    @require_restaurant
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        restaurant = get_current_restaurant(self.request)
        today = timezone.now().date()
        return Reservation.objects.filter(
            restaurant=restaurant,
            reservation_date=today,
            status__in=["pending", "confirmed", "waitlist", "seated"],
        ).order_by("reservation_time")


class DashboardUpcomingReservationsView(generics.ListAPIView):
    """Get upcoming reservations."""

    serializer_class = ReservationListSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff]

    @require_restaurant
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        restaurant = get_current_restaurant(self.request)
        return Reservation.objects.filter(
            restaurant=restaurant,
            reservation_date__gte=timezone.now().date(),
            status__in=["pending", "confirmed", "waitlist"],
        ).order_by("reservation_date", "reservation_time")[:20]


class DashboardBlockedTimeListView(generics.ListCreateAPIView):
    """List and create blocked times."""

    serializer_class = ReservationBlockedTimeSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff]

    @require_restaurant
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @require_restaurant
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def get_queryset(self):
        restaurant = get_current_restaurant(self.request)
        return ReservationBlockedTime.objects.filter(restaurant=restaurant)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["restaurant"] = get_current_restaurant(self.request)
        return context


class DashboardBlockedTimeDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Get, update, or delete a blocked time."""

    serializer_class = ReservationBlockedTimeSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff]

    @require_restaurant
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @require_restaurant
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)

    @require_restaurant
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)

    @require_restaurant
    def delete(self, request, *args, **kwargs):
        return super().delete(request, *args, **kwargs)

    def get_queryset(self):
        restaurant = get_current_restaurant(self.request)
        return ReservationBlockedTime.objects.filter(restaurant=restaurant)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["restaurant"] = get_current_restaurant(self.request)
        return context


class DashboardReservationStatsView(APIView):
    """Get reservation statistics."""

    permission_classes = [IsAuthenticated, IsTenantStaff]

    @require_restaurant
    def get(self, request):
        restaurant = get_current_restaurant(request)
        today = timezone.now().date()

        # Today's stats
        today_reservations = Reservation.objects.filter(
            restaurant=restaurant,
            reservation_date=today,
        )

        # This week's stats
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        week_reservations = Reservation.objects.filter(
            restaurant=restaurant,
            reservation_date__gte=week_start,
            reservation_date__lte=week_end,
        )

        # This month's stats
        month_start = today.replace(day=1)
        month_reservations = Reservation.objects.filter(
            restaurant=restaurant,
            reservation_date__gte=month_start,
            reservation_date__lte=today,
        )

        return Response(
            {
                "today": {
                    "total": today_reservations.count(),
                    "pending": today_reservations.filter(status="pending").count(),
                    "confirmed": today_reservations.filter(status="confirmed").count(),
                    "seated": today_reservations.filter(status="seated").count(),
                    "completed": today_reservations.filter(status="completed").count(),
                    "cancelled": today_reservations.filter(status="cancelled").count(),
                    "no_show": today_reservations.filter(status="no_show").count(),
                    "total_guests": sum(r.party_size for r in today_reservations),
                },
                "this_week": {
                    "total": week_reservations.count(),
                    "confirmed": week_reservations.filter(status__in=["confirmed", "completed", "seated"]).count(),
                    "cancelled": week_reservations.filter(status="cancelled").count(),
                    "no_show": week_reservations.filter(status="no_show").count(),
                },
                "this_month": {
                    "total": month_reservations.count(),
                    "completed": month_reservations.filter(status="completed").count(),
                    "no_show_rate": (
                        month_reservations.filter(status="no_show").count() / month_reservations.count() * 100
                        if month_reservations.count() > 0
                        else 0
                    ),
                },
            }
        )
