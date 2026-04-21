"""
Serializers for the reviews app.
"""

from rest_framework import serializers

from apps.orders.models import Order
from apps.staff.models import StaffMember

from .models import (
    MAX_IMAGES_PER_REVIEW,
    MAX_VIDEO_DURATION_S,
    MAX_VIDEOS_PER_REVIEW,
    Review,
    ReviewMedia,
    ReviewReport,
)


class ReviewMediaSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = ReviewMedia
        fields = [
            "id",
            "kind",
            "file_url",
            "blurhash",
            "duration_s",
            "position",
            "created_at",
        ]
        read_only_fields = fields

    def get_file_url(self, obj) -> str:
        if not obj.file:
            return ""
        try:
            return obj.file.url
        except Exception:
            return ""


class ReviewSerializer(serializers.ModelSerializer):
    """Public-facing serializer: user's display bits + media + flags."""

    user_id = serializers.UUIDField(source="user.id", read_only=True)
    user_name = serializers.SerializerMethodField()
    user_avatar = serializers.SerializerMethodField()
    restaurant_id = serializers.UUIDField(read_only=True)
    restaurant_slug = serializers.CharField(source="restaurant.slug", read_only=True)
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    media = ReviewMediaSerializer(many=True, read_only=True)
    is_editable = serializers.BooleanField(read_only=True)
    edit_locks_at = serializers.DateTimeField(read_only=True)
    is_mine = serializers.SerializerMethodField()
    can_report = serializers.SerializerMethodField()
    open_reports = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = [
            "id",
            "restaurant_id",
            "restaurant_slug",
            "order_number",
            "user_id",
            "user_name",
            "user_avatar",
            "rating",
            "title",
            "body",
            "media",
            "is_hidden",
            "edited_at",
            "edit_locks_at",
            "is_editable",
            "is_mine",
            "can_report",
            "open_reports",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_user_name(self, obj) -> str:
        user = obj.user
        if not user:
            return ""
        return user.full_name or user.first_name or user.email.split("@")[0]

    def get_user_avatar(self, obj) -> str:
        user = obj.user
        if not user or not getattr(user, "avatar", None):
            return ""
        try:
            return user.avatar.url
        except Exception:
            return ""

    def _current_user(self):
        request = self.context.get("request")
        return getattr(request, "user", None) if request else None

    def get_is_mine(self, obj) -> bool:
        user = self._current_user()
        return bool(user and user.is_authenticated and obj.user_id == user.id)

    def get_can_report(self, obj) -> bool:
        # Frontend uses this to decide whether to render the Report icon.
        # We mirror IsStaffOfReviewedRestaurant here so it stays in sync.
        user = self._current_user()
        if not user or not user.is_authenticated:
            return False
        return StaffMember.objects.filter(
            user=user,
            restaurant_id=obj.restaurant_id,
            is_active=True,
        ).exists()

    def get_open_reports(self, obj) -> int:
        # Only exposed to staff (avoids leaking moderation state publicly).
        if not self.get_can_report(obj):
            return 0
        return obj.reports.filter(resolution=ReviewReport.RESOLUTION_NONE).count()


class ReviewCreateSerializer(serializers.ModelSerializer):
    """Create a review for a completed order owned by the current user."""

    class Meta:
        model = Review
        fields = ["order", "rating", "title", "body"]

    def validate_order(self, order):
        user = self.context["request"].user
        if order.customer_id != user.id:
            raise serializers.ValidationError("This order does not belong to you.")
        if order.status not in ("completed", "served"):
            raise serializers.ValidationError("You can only review a completed order.")
        if Review.objects.filter(order=order).exists():
            raise serializers.ValidationError("This order already has a review.")
        return order

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        validated_data["restaurant"] = validated_data["order"].restaurant
        return super().create(validated_data)


class ReviewUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["rating", "title", "body"]


class ReviewMediaUploadSerializer(serializers.Serializer):
    """Payload for POST /reviews/<id>/media/ — multipart only."""

    kind = serializers.ChoiceField(choices=ReviewMedia.KIND_CHOICES)
    file = serializers.FileField()
    duration_s = serializers.IntegerField(required=False, min_value=0)

    def validate(self, attrs):
        kind = attrs["kind"]
        f = attrs["file"]
        size = getattr(f, "size", 0) or 0
        if kind == ReviewMedia.IMAGE:
            if size > 5 * 1024 * 1024:
                raise serializers.ValidationError({"file": "Image must be ≤5 MB."})
        else:
            if size > 30 * 1024 * 1024:
                raise serializers.ValidationError({"file": "Video must be ≤30 MB."})
            duration = attrs.get("duration_s") or 0
            if duration > MAX_VIDEO_DURATION_S:
                raise serializers.ValidationError({"duration_s": f"Video must be ≤{MAX_VIDEO_DURATION_S} s."})
        return attrs


class EligibleOrderSerializer(serializers.ModelSerializer):
    """Minimal fields the write-a-review flow needs about a completed order."""

    restaurant_slug = serializers.CharField(source="restaurant.slug", read_only=True)
    restaurant_name = serializers.CharField(source="restaurant.name", read_only=True)
    restaurant_logo = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "restaurant",
            "restaurant_slug",
            "restaurant_name",
            "restaurant_logo",
            "total",
            "status",
            "completed_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_restaurant_logo(self, obj) -> str:
        logo = getattr(obj.restaurant, "logo", None)
        if not logo:
            return ""
        try:
            return logo.url
        except Exception:
            return ""


class ReviewReportCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewReport
        fields = ["reason", "notes"]


class ReviewStatsSerializer(serializers.Serializer):
    average = serializers.FloatField()
    total = serializers.IntegerField()
    distribution = serializers.DictField(child=serializers.IntegerField())


# Re-export caps so the frontend can consume them from the OpenAPI
# `components.schemas.Limits`-like object if it ever wires one up.
__all__ = [
    "ReviewSerializer",
    "ReviewCreateSerializer",
    "ReviewUpdateSerializer",
    "ReviewMediaSerializer",
    "ReviewMediaUploadSerializer",
    "EligibleOrderSerializer",
    "ReviewReportCreateSerializer",
    "ReviewStatsSerializer",
    "MAX_IMAGES_PER_REVIEW",
    "MAX_VIDEOS_PER_REVIEW",
    "MAX_VIDEO_DURATION_S",
]
