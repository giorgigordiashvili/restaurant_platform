"""
Views for favorites app.
"""

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.menu.models import MenuItem
from apps.tenants.models import Restaurant

from .models import FavoriteMenuItem, FavoriteRestaurant
from .serializers import (
    BulkFavoriteMenuItemSerializer,
    BulkFavoriteRestaurantSerializer,
    FavoriteMenuItemCreateSerializer,
    FavoriteMenuItemSerializer,
    FavoriteRestaurantCreateSerializer,
    FavoriteRestaurantSerializer,
    FavoriteStatusSerializer,
)

# ============== Favorite Restaurant Views ==============


class FavoriteRestaurantListView(generics.ListAPIView):
    """List user's favorite restaurants."""

    serializer_class = FavoriteRestaurantSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return FavoriteRestaurant.objects.filter(user=self.request.user).select_related("restaurant")


class FavoriteRestaurantCreateView(generics.CreateAPIView):
    """Add a restaurant to favorites."""

    serializer_class = FavoriteRestaurantCreateSerializer
    permission_classes = [IsAuthenticated]


class FavoriteRestaurantDeleteView(generics.DestroyAPIView):
    """Remove a restaurant from favorites."""

    permission_classes = [IsAuthenticated]
    lookup_field = "restaurant_id"
    lookup_url_kwarg = "restaurant_id"

    def get_queryset(self):
        return FavoriteRestaurant.objects.filter(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_queryset().get(restaurant_id=kwargs["restaurant_id"])
            instance.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except FavoriteRestaurant.DoesNotExist:
            return Response(
                {"detail": "Restaurant not in favorites."},
                status=status.HTTP_404_NOT_FOUND,
            )


class FavoriteRestaurantToggleView(APIView):
    """Toggle a restaurant's favorite status."""

    permission_classes = [IsAuthenticated]

    def post(self, request, restaurant_id):
        try:
            restaurant = Restaurant.objects.get(id=restaurant_id, is_active=True)
        except Restaurant.DoesNotExist:
            return Response(
                {"detail": "Restaurant not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        favorite, created = FavoriteRestaurant.objects.get_or_create(
            user=request.user,
            restaurant=restaurant,
        )

        if not created:
            favorite.delete()
            return Response(
                {"is_favorited": False, "message": "Removed from favorites."},
                status=status.HTTP_200_OK,
            )

        return Response(
            {"is_favorited": True, "message": "Added to favorites."},
            status=status.HTTP_201_CREATED,
        )


class FavoriteRestaurantStatusView(APIView):
    """Check if a restaurant is favorited."""

    permission_classes = [IsAuthenticated]

    def get(self, request, restaurant_id):
        favorite = FavoriteRestaurant.objects.filter(
            user=request.user,
            restaurant_id=restaurant_id,
        ).first()

        serializer = FavoriteStatusSerializer(
            {
                "is_favorited": favorite is not None,
                "favorite_id": favorite.id if favorite else None,
            }
        )
        return Response(serializer.data)


class BulkFavoriteRestaurantStatusView(APIView):
    """Check favorite status for multiple restaurants."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = BulkFavoriteRestaurantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        restaurant_ids = serializer.validated_data["restaurant_ids"]
        favorites = FavoriteRestaurant.objects.filter(
            user=request.user,
            restaurant_id__in=restaurant_ids,
        ).values_list("restaurant_id", flat=True)

        result = {str(rid): rid in favorites for rid in restaurant_ids}
        return Response(result)


# ============== Favorite Menu Item Views ==============


class FavoriteMenuItemListView(generics.ListAPIView):
    """List user's favorite menu items."""

    serializer_class = FavoriteMenuItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = FavoriteMenuItem.objects.filter(user=self.request.user).select_related("menu_item", "restaurant")

        # Filter by restaurant if provided
        restaurant_id = self.request.query_params.get("restaurant")
        if restaurant_id:
            queryset = queryset.filter(restaurant_id=restaurant_id)

        return queryset


class FavoriteMenuItemCreateView(generics.CreateAPIView):
    """Add a menu item to favorites."""

    serializer_class = FavoriteMenuItemCreateSerializer
    permission_classes = [IsAuthenticated]


class FavoriteMenuItemDeleteView(generics.DestroyAPIView):
    """Remove a menu item from favorites."""

    permission_classes = [IsAuthenticated]
    lookup_field = "menu_item_id"
    lookup_url_kwarg = "menu_item_id"

    def get_queryset(self):
        return FavoriteMenuItem.objects.filter(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_queryset().get(menu_item_id=kwargs["menu_item_id"])
            instance.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except FavoriteMenuItem.DoesNotExist:
            return Response(
                {"detail": "Menu item not in favorites."},
                status=status.HTTP_404_NOT_FOUND,
            )


class FavoriteMenuItemToggleView(APIView):
    """Toggle a menu item's favorite status."""

    permission_classes = [IsAuthenticated]

    def post(self, request, menu_item_id):
        try:
            menu_item = MenuItem.objects.select_related("restaurant").get(
                id=menu_item_id,
                is_available=True,
                restaurant__is_active=True,
            )
        except MenuItem.DoesNotExist:
            return Response(
                {"detail": "Menu item not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        favorite, created = FavoriteMenuItem.objects.get_or_create(
            user=request.user,
            menu_item=menu_item,
            defaults={"restaurant": menu_item.restaurant},
        )

        if not created:
            favorite.delete()
            return Response(
                {"is_favorited": False, "message": "Removed from favorites."},
                status=status.HTTP_200_OK,
            )

        return Response(
            {"is_favorited": True, "message": "Added to favorites."},
            status=status.HTTP_201_CREATED,
        )


class FavoriteMenuItemStatusView(APIView):
    """Check if a menu item is favorited."""

    permission_classes = [IsAuthenticated]

    def get(self, request, menu_item_id):
        favorite = FavoriteMenuItem.objects.filter(
            user=request.user,
            menu_item_id=menu_item_id,
        ).first()

        serializer = FavoriteStatusSerializer(
            {
                "is_favorited": favorite is not None,
                "favorite_id": favorite.id if favorite else None,
            }
        )
        return Response(serializer.data)


class BulkFavoriteMenuItemStatusView(APIView):
    """Check favorite status for multiple menu items."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = BulkFavoriteMenuItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        menu_item_ids = serializer.validated_data["menu_item_ids"]
        favorites = FavoriteMenuItem.objects.filter(
            user=request.user,
            menu_item_id__in=menu_item_ids,
        ).values_list("menu_item_id", flat=True)

        result = {str(mid): mid in favorites for mid in menu_item_ids}
        return Response(result)


# ============== Combined Views ==============


class FavoriteCountsView(APIView):
    """Get counts of user's favorites."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "restaurants": FavoriteRestaurant.objects.filter(user=request.user).count(),
                "menu_items": FavoriteMenuItem.objects.filter(user=request.user).count(),
            }
        )


class ClearAllFavoritesView(APIView):
    """Clear all user's favorites."""

    permission_classes = [IsAuthenticated]

    def delete(self, request):
        restaurants_deleted = FavoriteRestaurant.objects.filter(user=request.user).delete()[0]
        menu_items_deleted = FavoriteMenuItem.objects.filter(user=request.user).delete()[0]

        return Response(
            {
                "message": "All favorites cleared.",
                "restaurants_removed": restaurants_deleted,
                "menu_items_removed": menu_items_deleted,
            }
        )
