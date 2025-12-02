"""
Serializers for favorites app.
"""

from rest_framework import serializers

from apps.menu.models import MenuItem
from apps.tenants.models import Restaurant

from .models import FavoriteMenuItem, FavoriteRestaurant


class FavoriteRestaurantSerializer(serializers.ModelSerializer):
    """Serializer for favorite restaurants."""

    restaurant_name = serializers.CharField(source="restaurant.name", read_only=True)
    restaurant_slug = serializers.CharField(source="restaurant.slug", read_only=True)
    restaurant_logo = serializers.ImageField(source="restaurant.logo", read_only=True)
    restaurant_city = serializers.CharField(source="restaurant.city", read_only=True)
    restaurant_cuisine_type = serializers.CharField(source="restaurant.cuisine_type", read_only=True)

    class Meta:
        model = FavoriteRestaurant
        fields = [
            "id",
            "restaurant",
            "restaurant_name",
            "restaurant_slug",
            "restaurant_logo",
            "restaurant_city",
            "restaurant_cuisine_type",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class FavoriteRestaurantCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating favorite restaurants."""

    class Meta:
        model = FavoriteRestaurant
        fields = ["restaurant"]

    def validate_restaurant(self, value):
        """Validate restaurant is active."""
        if not value.is_active:
            raise serializers.ValidationError("Restaurant is not active.")
        return value

    def validate(self, attrs):
        """Check for duplicate favorites."""
        user = self.context["request"].user
        restaurant = attrs["restaurant"]

        if FavoriteRestaurant.objects.filter(user=user, restaurant=restaurant).exists():
            raise serializers.ValidationError({"restaurant": "This restaurant is already in your favorites."})

        return attrs

    def create(self, validated_data):
        """Create favorite with current user."""
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class FavoriteMenuItemSerializer(serializers.ModelSerializer):
    """Serializer for favorite menu items."""

    menu_item_name = serializers.SerializerMethodField()
    menu_item_price = serializers.DecimalField(
        source="menu_item.price",
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )
    menu_item_image = serializers.ImageField(source="menu_item.image", read_only=True)
    restaurant_name = serializers.CharField(source="restaurant.name", read_only=True)
    restaurant_slug = serializers.CharField(source="restaurant.slug", read_only=True)
    is_available = serializers.BooleanField(source="menu_item.is_available", read_only=True)

    class Meta:
        model = FavoriteMenuItem
        fields = [
            "id",
            "menu_item",
            "menu_item_name",
            "menu_item_price",
            "menu_item_image",
            "restaurant",
            "restaurant_name",
            "restaurant_slug",
            "is_available",
            "created_at",
        ]
        read_only_fields = ["id", "restaurant", "created_at"]

    def get_menu_item_name(self, obj):
        """Get translated menu item name."""
        return str(obj.menu_item) if obj.menu_item else None


class FavoriteMenuItemCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating favorite menu items."""

    class Meta:
        model = FavoriteMenuItem
        fields = ["menu_item"]

    def validate_menu_item(self, value):
        """Validate menu item is available."""
        if not value.is_available:
            raise serializers.ValidationError("Menu item is not available.")
        if not value.restaurant.is_active:
            raise serializers.ValidationError("Restaurant is not active.")
        return value

    def validate(self, attrs):
        """Check for duplicate favorites."""
        user = self.context["request"].user
        menu_item = attrs["menu_item"]

        if FavoriteMenuItem.objects.filter(user=user, menu_item=menu_item).exists():
            raise serializers.ValidationError({"menu_item": "This menu item is already in your favorites."})

        return attrs

    def create(self, validated_data):
        """Create favorite with current user and restaurant."""
        validated_data["user"] = self.context["request"].user
        validated_data["restaurant"] = validated_data["menu_item"].restaurant
        return super().create(validated_data)


class FavoriteStatusSerializer(serializers.Serializer):
    """Serializer for checking favorite status."""

    is_favorited = serializers.BooleanField(read_only=True)
    favorite_id = serializers.UUIDField(read_only=True, allow_null=True)


class BulkFavoriteRestaurantSerializer(serializers.Serializer):
    """Serializer for bulk favorite restaurant operations."""

    restaurant_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=50,
    )

    def validate_restaurant_ids(self, value):
        """Validate all restaurants exist and are active."""
        restaurants = Restaurant.objects.filter(id__in=value, is_active=True)
        if len(restaurants) != len(value):
            raise serializers.ValidationError("Some restaurants do not exist or are inactive.")
        return value


class BulkFavoriteMenuItemSerializer(serializers.Serializer):
    """Serializer for bulk favorite menu item operations."""

    menu_item_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=50,
    )

    def validate_menu_item_ids(self, value):
        """Validate all menu items exist and are available."""
        items = MenuItem.objects.filter(id__in=value, is_available=True, restaurant__is_active=True)
        if len(items) != len(value):
            raise serializers.ValidationError("Some menu items do not exist or are unavailable.")
        return value
