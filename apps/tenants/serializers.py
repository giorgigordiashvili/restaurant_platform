"""
Restaurant (tenant) serializers.
"""

from rest_framework import serializers

from apps.accounts.serializers import UserSerializer

from .models import Restaurant, RestaurantCategory, RestaurantHours


class RestaurantCategorySerializer(serializers.ModelSerializer):
    """Serializer for restaurant categories."""

    class Meta:
        model = RestaurantCategory
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "icon",
            "image",
        ]
        read_only_fields = ["id", "slug"]


class RestaurantHoursSerializer(serializers.ModelSerializer):
    """Serializer for restaurant operating hours."""

    day_name = serializers.SerializerMethodField()

    class Meta:
        model = RestaurantHours
        fields = [
            "id",
            "day_of_week",
            "day_name",
            "open_time",
            "close_time",
            "is_closed",
        ]
        read_only_fields = ["id"]

    def get_day_name(self, obj):
        return obj.get_day_of_week_display()


class RestaurantListSerializer(serializers.ModelSerializer):
    """Minimal serializer for restaurant lists."""

    is_open_now = serializers.SerializerMethodField()
    category = RestaurantCategorySerializer(read_only=True)

    class Meta:
        model = Restaurant
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "logo",
            "city",
            "category",
            "average_rating",
            "total_reviews",
            "is_open_now",
            "accepts_remote_orders",
            "accepts_reservations",
        ]

    def get_is_open_now(self, obj):
        return obj.is_open_now


class RestaurantDetailSerializer(serializers.ModelSerializer):
    """Full serializer for restaurant details."""

    operating_hours = RestaurantHoursSerializer(many=True, read_only=True)
    is_open_now = serializers.SerializerMethodField()
    full_address = serializers.SerializerMethodField()
    owner = UserSerializer(read_only=True)
    category = RestaurantCategorySerializer(read_only=True)

    class Meta:
        model = Restaurant
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "category",
            "is_active",
            "owner",
            # Contact
            "email",
            "phone",
            "website",
            # Address
            "address",
            "city",
            "postal_code",
            "country",
            "full_address",
            "latitude",
            "longitude",
            # Branding
            "logo",
            "cover_image",
            "primary_color",
            "secondary_color",
            # Settings
            "default_currency",
            "timezone",
            "default_language",
            "tax_rate",
            "service_charge",
            # Features
            "accepts_remote_orders",
            "accepts_reservations",
            "accepts_takeaway",
            # Stats
            "average_rating",
            "total_reviews",
            "total_orders",
            "minimum_order_amount",
            "average_preparation_time",
            # Hours
            "operating_hours",
            "is_open_now",
            # Meta
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "slug",
            "owner",
            "average_rating",
            "total_reviews",
            "total_orders",
            "created_at",
            "updated_at",
        ]

    def get_is_open_now(self, obj):
        return obj.is_open_now

    def get_full_address(self, obj):
        return obj.full_address


class RestaurantCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a new restaurant."""

    category_id = serializers.PrimaryKeyRelatedField(
        queryset=RestaurantCategory.objects.filter(is_active=True),
        source="category",
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Restaurant
        fields = [
            "name",
            "slug",
            "description",
            "category_id",
            "email",
            "phone",
            "website",
            "address",
            "city",
            "postal_code",
            "country",
        ]

    def validate_slug(self, value):
        """Ensure slug is unique."""
        if Restaurant.objects.filter(slug=value).exists():
            raise serializers.ValidationError("A restaurant with this slug already exists.")
        return value

    def create(self, validated_data):
        """Create restaurant and set current user as owner."""
        user = self.context["request"].user
        restaurant = Restaurant.objects.create(owner=user, **validated_data)

        # Create default operating hours
        RestaurantHours.create_default_hours(restaurant)

        # Create default staff roles
        from apps.staff.models import StaffRole

        StaffRole.create_default_roles(restaurant)

        return restaurant


class RestaurantUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating restaurant settings."""

    class Meta:
        model = Restaurant
        fields = [
            "name",
            "description",
            "email",
            "phone",
            "website",
            "address",
            "city",
            "postal_code",
            "country",
            "latitude",
            "longitude",
            "logo",
            "cover_image",
            "primary_color",
            "secondary_color",
            "default_currency",
            "timezone",
            "default_language",
            "tax_rate",
            "service_charge",
            "accepts_remote_orders",
            "accepts_reservations",
            "accepts_takeaway",
            "minimum_order_amount",
            "average_preparation_time",
        ]


class RestaurantHoursUpdateSerializer(serializers.Serializer):
    """Serializer for updating all operating hours at once."""

    hours = RestaurantHoursSerializer(many=True)

    def validate_hours(self, value):
        """Validate hours data."""
        days_seen = set()
        for hour in value:
            day = hour.get("day_of_week")
            if day in days_seen:
                raise serializers.ValidationError(f"Duplicate entry for day {day}")
            if day < 0 or day > 6:
                raise serializers.ValidationError("day_of_week must be between 0 and 6")
            days_seen.add(day)
        return value

    def save(self, restaurant):
        """Update operating hours."""
        hours_data = self.validated_data["hours"]

        for hour_data in hours_data:
            day = hour_data["day_of_week"]
            RestaurantHours.objects.update_or_create(
                restaurant=restaurant,
                day_of_week=day,
                defaults={
                    "open_time": hour_data["open_time"],
                    "close_time": hour_data["close_time"],
                    "is_closed": hour_data.get("is_closed", False),
                },
            )

        return restaurant.operating_hours.all()
