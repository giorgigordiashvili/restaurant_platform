from __future__ import annotations

from rest_framework import serializers

from apps.giftcards.models import GiftCard, GiftCardTransaction


class GiftCardSerializer(serializers.ModelSerializer):
    masked_code = serializers.CharField(read_only=True)
    is_usable = serializers.BooleanField(read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = GiftCard
        fields = [
            "id",
            "code",
            "masked_code",
            "initial_value",
            "balance",
            "currency",
            "status",
            "status_display",
            "is_usable",
            "kind",
            "design",
            "expires_at",
            "purchaser_name",
            "purchaser_phone",
            "recipient_name",
            "recipient_phone",
            "recipient_email",
            "message",
            "sold_online",
            "delivered_at",
            "created_at",
        ]
        read_only_fields = fields


class GiftCardLookupSerializer(serializers.Serializer):
    """Balance check at the till: no purchaser data, masked code."""

    id = serializers.UUIDField()
    masked_code = serializers.CharField()
    balance = serializers.DecimalField(max_digits=10, decimal_places=2)
    currency = serializers.CharField()
    status = serializers.CharField()
    is_usable = serializers.BooleanField()
    expires_at = serializers.DateTimeField(allow_null=True)
    error_code = serializers.CharField(allow_blank=True)


class GiftCardTransactionSerializer(serializers.ModelSerializer):
    order_number = serializers.SerializerMethodField()

    class Meta:
        model = GiftCardTransaction
        fields = ["id", "kind", "amount", "balance_after", "order_number", "note", "created_at"]
        read_only_fields = fields

    def get_order_number(self, obj):
        return obj.order.order_number if obj.order_id else ""


class SellSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=1)
    method = serializers.ChoiceField(choices=["cash", "card_terminal", "other"])
    tendered = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    kind = serializers.ChoiceField(choices=["physical", "digital"], default="physical")
    design = serializers.CharField(max_length=12, required=False, default="classic")
    recipient_name = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    recipient_phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    recipient_email = serializers.EmailField(required=False, allow_blank=True, default="")
    purchaser_name = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    purchaser_phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    message = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class RedeemSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    order_id = serializers.UUIDField(required=False)
    order_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    session_id = serializers.UUIDField(required=False)

    def validate(self, data):
        targets = [k for k in ("order_id", "order_ids", "session_id") if data.get(k)]
        if len(targets) != 1:
            raise serializers.ValidationError("Give exactly one of order_id, order_ids or session_id.")
        return data


class AdjustSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    note = serializers.CharField(max_length=200)


class VoidSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class PublicBalanceSerializer(serializers.Serializer):
    masked_code = serializers.CharField()
    balance = serializers.DecimalField(max_digits=10, decimal_places=2)
    currency = serializers.CharField()
    status = serializers.CharField()
    expires_at = serializers.DateTimeField(allow_null=True)
    restaurant = serializers.CharField()


class SummarySerializer(serializers.Serializer):
    outstanding = serializers.DecimalField(max_digits=12, decimal_places=2)
    active = serializers.IntegerField()
    sold_today = serializers.IntegerField()
    redeemed_today = serializers.DecimalField(max_digits=12, decimal_places=2)
