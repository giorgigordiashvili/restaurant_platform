"""
Public restaurant discovery views.
"""

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters, generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .filters import RestaurantFilter
from .models import Restaurant
from .serializers import (
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
        return Restaurant.objects.filter(is_active=True).prefetch_related("operating_hours")


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
