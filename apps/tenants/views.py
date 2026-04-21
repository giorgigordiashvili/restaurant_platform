"""
Public restaurant discovery views.
"""

from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from rest_framework import filters, generics, serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema

from .filters import RestaurantFilter
from .models import Amenity, City, Restaurant, RestaurantCategory
from .serializers import (
    AmenitySerializer,
    CitySerializer,
    RestaurantCategorySerializer,
    RestaurantCreateSerializer,
    RestaurantDetailSerializer,
    RestaurantHoursSerializer,
    RestaurantListSerializer,
)


@extend_schema(tags=["Restaurants"])
class RestaurantListView(generics.ListAPIView):
    """List all active restaurants with filtering and search."""

    serializer_class = RestaurantListSerializer
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = RestaurantFilter
    search_fields = ["name", "description", "city"]
    ordering_fields = ["name", "average_rating", "created_at"]
    ordering = ["-average_rating"]

    def get_queryset(self):
        return Restaurant.objects.filter(is_active=True).select_related(
            "city_obj"
        ).prefetch_related("operating_hours")


@extend_schema(tags=["Restaurants"])
class RestaurantDetailView(generics.RetrieveAPIView):
    """Get restaurant details by slug."""

    serializer_class = RestaurantDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"

    def get_queryset(self):
        return Restaurant.objects.filter(is_active=True).prefetch_related("operating_hours")


@extend_schema(tags=["Restaurants"])
class RestaurantHoursView(APIView):
    """Get restaurant operating hours."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        try:
            restaurant = Restaurant.objects.prefetch_related("operating_hours").get(slug=slug, is_active=True)
            hours = restaurant.operating_hours.all()
            serializer = RestaurantHoursSerializer(hours, many=True)
            return Response(
                {
                    "success": True,
                    "data": {
                        "restaurant": restaurant.name,
                        "is_open_now": restaurant.is_open_now,
                        "hours": serializer.data,
                    },
                }
            )
        except Restaurant.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Restaurant not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )


@extend_schema(tags=["Restaurants"])
class RestaurantCreateView(generics.CreateAPIView):
    """Create a new restaurant (authenticated users)."""

    serializer_class = RestaurantCreateSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        restaurant = serializer.save()

        return Response(
            {
                "success": True,
                "message": "Restaurant created successfully.",
                "data": RestaurantDetailSerializer(restaurant).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Restaurants"])
class RestaurantCategoryListView(generics.ListAPIView):
    """Public list of active restaurant categories — used by the signup form."""

    serializer_class = RestaurantCategorySerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        return RestaurantCategory.objects.filter(is_active=True).order_by(
            "display_order", "slug"
        )


@extend_schema(tags=["Restaurants"])
class AmenityListView(generics.ListAPIView):
    """Public list of active amenities."""

    serializer_class = AmenitySerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        return Amenity.objects.filter(is_active=True).order_by("display_order", "slug")


@extend_schema(tags=["Restaurants"])
class RestaurantCitiesView(APIView):
    """List all cities available for restaurant selection."""

    permission_classes = [AllowAny]

    def get(self, request):
        try:
            cities = list(
                City.objects.filter(is_active=True)
                .order_by("display_order", "slug")
                .values("id", "slug", "country")
            )

            # Fetch translations separately
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT master_id, language_code, name FROM cities_translation"
                )
                translations = cursor.fetchall()

            # Build translation map
            trans_map = {}
            for master_id, lang, name in translations:
                if master_id not in trans_map:
                    trans_map[master_id] = {}
                trans_map[master_id][lang] = {"name": name}

            # Merge
            for city in cities:
                city["translations"] = trans_map.get(city["id"], {})

            return Response(
                {
                    "success": True,
                    "data": {
                        "count": len(cities),
                        "cities": cities,
                    },
                }
            )
        except Exception as e:
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "cities_error",
                        "message": str(e),
                    },
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RestaurantSearchSerializer(serializers.Serializer):
    """Validates search parameters for homepage restaurant search."""

    city = serializers.CharField(required=False, help_text="City name (case-insensitive)")
    date = serializers.DateField(required=False, help_text="Reservation date (YYYY-MM-DD)")
    time = serializers.TimeField(required=False, help_text="Desired reservation time (HH:MM)")
    party_size = serializers.IntegerField(
        required=False, min_value=1, max_value=50, help_text="Number of guests"
    )
    search = serializers.CharField(required=False, help_text="Search by restaurant name")


@extend_schema(
    tags=["Restaurants"],
    parameters=[
        OpenApiParameter(name="city", type=str, required=False, description="City name"),
        OpenApiParameter(name="date", type=str, required=False, description="Date (YYYY-MM-DD)"),
        OpenApiParameter(name="time", type=str, required=False, description="Time (HH:MM)"),
        OpenApiParameter(
            name="party_size", type=int, required=False, description="Number of guests"
        ),
        OpenApiParameter(name="search", type=str, required=False, description="Search by name"),
    ],
)
class RestaurantSearchView(APIView):
    """
    Search restaurants by city, date, time, and party size.

    Used by the homepage search bar. Returns restaurants that:
    - Match the city filter (if provided)
    - Are open on the given date/time (if provided)
    - Accept reservations and can accommodate the party size (if provided)
    - Match the search query (if provided)

    All parameters are optional — with no params, returns all active restaurants.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        params = RestaurantSearchSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        data = params.validated_data

        city = data.get("city")
        date = data.get("date")
        search_time = data.get("time")
        party_size = data.get("party_size")
        search = data.get("search")

        qs = Restaurant.objects.filter(is_active=True).select_related(
            "city_obj"
        ).prefetch_related("operating_hours", "amenities", "category")

        # Filter by city
        if city:
            qs = qs.filter(city__iexact=city)

        # Filter by name search
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))

        # Filter by date — restaurant must be open on that day of week
        if date:
            day_of_week = date.weekday()
            qs = qs.filter(
                operating_hours__day_of_week=day_of_week,
                operating_hours__is_closed=False,
            )

            # If time is also provided, check that it falls within operating hours
            if search_time:
                qs = qs.filter(
                    operating_hours__day_of_week=day_of_week,
                    operating_hours__open_time__lte=search_time,
                    operating_hours__close_time__gte=search_time,
                )

        # Filter by party size — restaurant must accept reservations
        # and party size must be within settings range
        if party_size:
            qs = qs.filter(accepts_reservations=True)
            # Exclude restaurants whose settings explicitly reject this party size
            qs = qs.exclude(
                reservation_settings__min_party_size__gt=party_size,
            )
            qs = qs.exclude(
                reservation_settings__max_party_size__lt=party_size,
            )

        # Check actual table availability if date + time + party_size are all provided
        if date and search_time and party_size:
            available_ids = []
            for restaurant in qs:
                if self._has_availability(restaurant, date, search_time, party_size):
                    available_ids.append(restaurant.id)
            qs = qs.filter(id__in=available_ids)

        qs = qs.distinct().order_by("-average_rating")

        serializer = RestaurantListSerializer(qs, many=True)
        return Response(
            {
                "success": True,
                "data": {
                    "count": len(serializer.data),
                    "results": serializer.data,
                    "filters_applied": {
                        "city": city,
                        "date": str(date) if date else None,
                        "time": str(search_time) if search_time else None,
                        "party_size": party_size,
                        "search": search,
                    },
                },
            }
        )

    def _has_availability(self, restaurant, date, search_time, party_size):
        """Check if a restaurant has at least one available table for the given slot."""
        from apps.reservations.models import Reservation, ReservationBlockedTime

        # Check blocked times
        slot_datetime = timezone.make_aware(datetime.combine(date, search_time))
        is_blocked = ReservationBlockedTime.objects.filter(
            restaurant=restaurant,
            start_datetime__lte=slot_datetime,
            end_datetime__gt=slot_datetime,
        ).exists()

        if is_blocked:
            return False

        # Count existing reservations at this slot
        existing = Reservation.objects.filter(
            restaurant=restaurant,
            reservation_date=date,
            reservation_time=search_time,
            status__in=["pending", "confirmed", "waitlist"],
        ).count()

        # Check if there are tables that can fit the party
        available_tables = restaurant.tables.filter(
            is_active=True,
            capacity__gte=party_size,
        ).count()

        return available_tables > existing
