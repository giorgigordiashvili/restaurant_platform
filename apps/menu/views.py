"""
Menu views for public access and dashboard management.
"""

from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantManager
from apps.tenants.models import Restaurant

from .models import MenuCategory, MenuItem, ModifierGroup
from .serializers import (
    CategoryReorderSerializer,
    FullMenuSerializer,
    MenuCategorySerializer,
    MenuItemCreateSerializer,
    MenuItemSerializer,
    MenuItemUpdateSerializer,
    ModifierGroupCreateSerializer,
    ModifierGroupSerializer,
)


# ============== Public Views ==============


@extend_schema(tags=["Menu"])
class PublicMenuView(APIView):
    """Get full menu for a restaurant (public)."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        try:
            restaurant = Restaurant.objects.get(slug=slug, is_active=True)
            serializer = FullMenuSerializer(restaurant)
            return Response(
                {
                    "success": True,
                    "data": {
                        "restaurant": restaurant.name,
                        "menu": serializer.data,
                    },
                }
            )
        except Restaurant.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Restaurant not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )


@extend_schema(tags=["Menu"])
class PublicMenuItemDetailView(generics.RetrieveAPIView):
    """Get menu item details (public)."""

    serializer_class = MenuItemSerializer
    permission_classes = [AllowAny]
    lookup_field = "id"

    def get_queryset(self):
        slug = self.kwargs.get("slug")
        return MenuItem.objects.filter(
            restaurant__slug=slug,
            restaurant__is_active=True,
            is_available=True,
        ).select_related("category", "restaurant")


# ============== Dashboard Views ==============


@extend_schema(tags=["Dashboard - Menu"])
class MenuCategoryListCreateView(generics.ListCreateAPIView):
    """List or create menu categories."""

    serializer_class = MenuCategorySerializer
    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("menu", "read")

    @require_restaurant
    def get_queryset(self):
        return MenuCategory.objects.filter(
            restaurant=self.request.restaurant
        ).order_by("display_order")

    @require_restaurant
    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.restaurant)


@extend_schema(tags=["Dashboard - Menu"])
class MenuCategoryDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Get, update, or delete a menu category."""

    serializer_class = MenuCategorySerializer
    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("menu", "update")
    lookup_field = "id"

    @require_restaurant
    def get_queryset(self):
        return MenuCategory.objects.filter(restaurant=self.request.restaurant)


@extend_schema(tags=["Dashboard - Menu"])
class MenuCategoryReorderView(APIView):
    """Reorder menu categories."""

    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("menu", "update")

    @require_restaurant
    def post(self, request):
        serializer = CategoryReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(restaurant=request.restaurant)

        return Response(
            {"success": True, "message": "Categories reordered."}
        )


@extend_schema(tags=["Dashboard - Menu"])
class MenuItemListCreateView(generics.ListCreateAPIView):
    """List or create menu items."""

    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("menu", "read")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return MenuItemCreateSerializer
        return MenuItemSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["restaurant"] = getattr(self.request, "restaurant", None)
        return context

    @require_restaurant
    def get_queryset(self):
        queryset = MenuItem.objects.filter(
            restaurant=self.request.restaurant
        ).select_related("category").prefetch_related(
            "modifier_groups_link__modifier_group"
        ).order_by("display_order")

        # Filter by category
        category_id = self.request.query_params.get("category")
        if category_id:
            queryset = queryset.filter(category_id=category_id)

        # Filter by availability
        available = self.request.query_params.get("available")
        if available is not None:
            queryset = queryset.filter(is_available=available.lower() == "true")

        # Filter by featured
        featured = self.request.query_params.get("featured")
        if featured is not None:
            queryset = queryset.filter(is_featured=featured.lower() == "true")

        return queryset

    @require_restaurant
    def perform_create(self, serializer):
        serializer.save()


@extend_schema(tags=["Dashboard - Menu"])
class MenuItemDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Get, update, or delete a menu item."""

    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("menu", "update")
    lookup_field = "id"

    def get_serializer_class(self):
        if self.request.method in ("PUT", "PATCH"):
            return MenuItemUpdateSerializer
        return MenuItemSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["restaurant"] = getattr(self.request, "restaurant", None)
        return context

    @require_restaurant
    def get_queryset(self):
        return MenuItem.objects.filter(
            restaurant=self.request.restaurant
        ).select_related("category").prefetch_related(
            "modifier_groups_link__modifier_group__modifiers"
        )


@extend_schema(tags=["Dashboard - Menu"])
class MenuItemImageUploadView(APIView):
    """Upload image for a menu item."""

    permission_classes = [IsAuthenticated, IsTenantManager]
    parser_classes = [MultiPartParser, FormParser]
    required_permission = ("menu", "update")

    @require_restaurant
    def post(self, request, id):
        try:
            item = MenuItem.objects.get(id=id, restaurant=request.restaurant)
        except MenuItem.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Menu item not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        if "image" not in request.FILES:
            return Response(
                {"success": False, "error": {"message": "No image provided."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Delete old image if exists
        if item.image:
            item.image.delete(save=False)

        item.image = request.FILES["image"]
        item.save(update_fields=["image", "updated_at"])

        return Response(
            {
                "success": True,
                "message": "Image uploaded.",
                "data": {"image": item.image.url if item.image else None},
            }
        )


@extend_schema(tags=["Dashboard - Menu"])
class ModifierGroupListCreateView(generics.ListCreateAPIView):
    """List or create modifier groups."""

    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("menu", "read")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ModifierGroupCreateSerializer
        return ModifierGroupSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["restaurant"] = getattr(self.request, "restaurant", None)
        return context

    @require_restaurant
    def get_queryset(self):
        return ModifierGroup.objects.filter(
            restaurant=self.request.restaurant
        ).prefetch_related("modifiers").order_by("display_order")

    @require_restaurant
    def perform_create(self, serializer):
        serializer.save()


@extend_schema(tags=["Dashboard - Menu"])
class ModifierGroupDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Get, update, or delete a modifier group."""

    serializer_class = ModifierGroupSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("menu", "update")
    lookup_field = "id"

    @require_restaurant
    def get_queryset(self):
        return ModifierGroup.objects.filter(
            restaurant=self.request.restaurant
        ).prefetch_related("modifiers")
