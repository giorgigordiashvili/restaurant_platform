from __future__ import annotations

from rest_framework import serializers

from .models import LoyaltyCounter, LoyaltyProgram, LoyaltyRedemption


class _MenuItemBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.SerializerMethodField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    image = serializers.ImageField(allow_null=True, required=False)

    def get_name(self, obj):
        # TranslatableModel provides translated name via safe_translation_getter
        try:
            return obj.safe_translation_getter("name", any_language=True)
        except Exception:
            return getattr(obj, "name", None)


class LoyaltyProgramSerializer(serializers.ModelSerializer):
    trigger_item_detail = _MenuItemBriefSerializer(source="trigger_item", read_only=True)
    reward_item_detail = _MenuItemBriefSerializer(source="reward_item", read_only=True)
    is_live = serializers.BooleanField(read_only=True)

    class Meta:
        model = LoyaltyProgram
        fields = [
            "id",
            "name",
            "description",
            "is_active",
            "is_live",
            "trigger_item",
            "trigger_item_detail",
            "threshold",
            "reward_item",
            "reward_item_detail",
            "reward_quantity",
            "starts_at",
            "ends_at",
            "code_ttl_seconds",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_live", "created_at", "updated_at"]


class LoyaltyProgramWriteSerializer(LoyaltyProgramSerializer):
    """Identical to read serializer, used on create/update — kept separate
    so we can tighten validation later without touching the read shape."""

    def validate(self, attrs):
        trigger = attrs.get("trigger_item")
        reward = attrs.get("reward_item")
        restaurant = self.context.get("restaurant")
        for field_name, item in (("trigger_item", trigger), ("reward_item", reward)):
            if item and restaurant and item.restaurant_id != restaurant.id:
                raise serializers.ValidationError(
                    {field_name: "Item must belong to this restaurant."}
                )
        if attrs.get("threshold") is not None and attrs["threshold"] < 1:
            raise serializers.ValidationError({"threshold": "Must be at least 1."})
        return attrs


class LoyaltyCounterSerializer(serializers.ModelSerializer):
    program = LoyaltyProgramSerializer(read_only=True)
    restaurant_name = serializers.CharField(source="program.restaurant.name", read_only=True)
    restaurant_slug = serializers.CharField(source="program.restaurant.slug", read_only=True)
    restaurant_logo = serializers.ImageField(source="program.restaurant.logo", read_only=True)
    can_redeem = serializers.BooleanField(read_only=True)

    class Meta:
        model = LoyaltyCounter
        fields = [
            "id",
            "program",
            "restaurant_name",
            "restaurant_slug",
            "restaurant_logo",
            "punches",
            "can_redeem",
            "last_earned_at",
            "created_at",
        ]
        read_only_fields = fields


class LoyaltyRedemptionSerializer(serializers.ModelSerializer):
    program = LoyaltyProgramSerializer(read_only=True)
    customer_name = serializers.SerializerMethodField()

    class Meta:
        model = LoyaltyRedemption
        fields = [
            "id",
            "code",
            "status",
            "program",
            "customer_name",
            "issued_at",
            "expires_at",
            "redeemed_at",
        ]
        read_only_fields = fields

    def get_customer_name(self, obj):
        if obj.user_id:
            return obj.user.get_full_name() or obj.user.email
        return obj.phone_number or "Anonymous"


class LoyaltyRedeemRequestSerializer(serializers.Serializer):
    program_id = serializers.UUIDField()


class LoyaltyValidateRequestSerializer(serializers.Serializer):
    code = serializers.CharField()


class LoyaltyConfirmRequestSerializer(serializers.Serializer):
    code = serializers.CharField()
    order_id = serializers.UUIDField(required=False, allow_null=True)
