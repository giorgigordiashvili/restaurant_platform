"""
Restaurant dashboard views (tenant-scoped).
"""

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import IsTenantManager

from .serializers import (
    RestaurantDetailSerializer,
    RestaurantHoursSerializer,
    RestaurantHoursUpdateSerializer,
    RestaurantUpdateSerializer,
)


@extend_schema(tags=["Dashboard - Settings"])
class RestaurantSettingsView(APIView):
    """Get or update restaurant settings."""

    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def get(self, request):
        serializer = RestaurantDetailSerializer(request.restaurant)
        return Response(
            {
                "success": True,
                "data": serializer.data,
            }
        )

    @require_restaurant
    def patch(self, request):
        serializer = RestaurantUpdateSerializer(
            request.restaurant,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {
                "success": True,
                "message": "Settings updated.",
                "data": RestaurantDetailSerializer(request.restaurant).data,
            }
        )


@extend_schema(tags=["Dashboard - Settings"])
class RestaurantHoursUpdateView(APIView):
    """Update restaurant operating hours."""

    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def get(self, request):
        hours = request.restaurant.operating_hours.all()
        serializer = RestaurantHoursSerializer(hours, many=True)
        return Response(
            {
                "success": True,
                "data": serializer.data,
            }
        )

    @require_restaurant
    def put(self, request):
        serializer = RestaurantHoursUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        hours = serializer.save(restaurant=request.restaurant)

        return Response(
            {
                "success": True,
                "message": "Operating hours updated.",
                "data": RestaurantHoursSerializer(hours, many=True).data,
            }
        )


@extend_schema(tags=["Dashboard - Settings"])
class RestaurantLogoUploadView(APIView):
    """Upload restaurant logo."""

    permission_classes = [IsAuthenticated, IsTenantManager]
    parser_classes = [MultiPartParser, FormParser]

    @require_restaurant
    def post(self, request):
        if "logo" not in request.FILES:
            return Response(
                {"success": False, "error": {"message": "No logo file provided."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        restaurant = request.restaurant
        # Delete old logo if exists
        if restaurant.logo:
            restaurant.logo.delete(save=False)

        restaurant.logo = request.FILES["logo"]
        restaurant.save(update_fields=["logo", "updated_at"])

        return Response(
            {
                "success": True,
                "message": "Logo uploaded.",
                "data": {"logo": restaurant.logo.url if restaurant.logo else None},
            }
        )

    @require_restaurant
    def delete(self, request):
        restaurant = request.restaurant
        if restaurant.logo:
            restaurant.logo.delete(save=True)

        return Response(
            {
                "success": True,
                "message": "Logo removed.",
            }
        )


@extend_schema(tags=["Dashboard - Settings"])
class RestaurantCoverUploadView(APIView):
    """Upload restaurant cover image."""

    permission_classes = [IsAuthenticated, IsTenantManager]
    parser_classes = [MultiPartParser, FormParser]

    @require_restaurant
    def post(self, request):
        if "cover" not in request.FILES:
            return Response(
                {"success": False, "error": {"message": "No cover image provided."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        restaurant = request.restaurant
        # Delete old cover if exists
        if restaurant.cover_image:
            restaurant.cover_image.delete(save=False)

        restaurant.cover_image = request.FILES["cover"]
        restaurant.save(update_fields=["cover_image", "updated_at"])

        return Response(
            {
                "success": True,
                "message": "Cover image uploaded.",
                "data": {"cover_image": restaurant.cover_image.url if restaurant.cover_image else None},
            }
        )

    @require_restaurant
    def delete(self, request):
        restaurant = request.restaurant
        if restaurant.cover_image:
            restaurant.cover_image.delete(save=True)

        return Response(
            {
                "success": True,
                "message": "Cover image removed.",
            }
        )
