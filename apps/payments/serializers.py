"""
Serializers for payments app.
"""

from rest_framework import serializers

from .models import Payment, PaymentMethod, Refund


class PaymentSerializer(serializers.ModelSerializer):
    """Serializer for payments."""

    order_number = serializers.CharField(source="order.order_number", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "order",
            "order_number",
            "amount",
            "tip_amount",
            "total_amount",
            "payment_method",
            "status",
            "currency",
            "receipt_number",
            "external_payment_id",
            "completed_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "total_amount",
            "receipt_number",
            "completed_at",
            "created_at",
        ]


class PaymentCreateSerializer(serializers.Serializer):
    """Serializer for creating payments."""

    order_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    tip_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, default=0)
    payment_method = serializers.ChoiceField(choices=Payment.PAYMENT_METHOD_CHOICES, default="card")
    payment_method_id = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_order_id(self, value):
        from apps.orders.models import Order

        restaurant = self.context.get("restaurant")
        try:
            order = Order.objects.get(id=value, restaurant=restaurant)
            return order
        except Order.DoesNotExist:
            raise serializers.ValidationError("Order not found.")


class PaymentIntentSerializer(serializers.Serializer):
    """Serializer for creating Stripe PaymentIntent."""

    order_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    tip_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, default=0)
    payment_method_id = serializers.CharField(required=False, allow_blank=True)
    save_payment_method = serializers.BooleanField(default=False)

    def validate_order_id(self, value):
        from apps.orders.models import Order

        restaurant = self.context.get("restaurant")
        try:
            order = Order.objects.get(id=value, restaurant=restaurant)
            return order
        except Order.DoesNotExist:
            raise serializers.ValidationError("Order not found.")


class CashPaymentSerializer(serializers.Serializer):
    """Serializer for cash payments."""

    order_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    tip_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, default=0)
    amount_received = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_order_id(self, value):
        from apps.orders.models import Order

        restaurant = self.context.get("restaurant")
        try:
            order = Order.objects.get(id=value, restaurant=restaurant)
            return order
        except Order.DoesNotExist:
            raise serializers.ValidationError("Order not found.")

    def validate(self, data):
        total = data["amount"] + data.get("tip_amount", 0)
        if data["amount_received"] < total:
            raise serializers.ValidationError({"amount_received": "Amount received is less than total."})
        return data


class RefundSerializer(serializers.ModelSerializer):
    """Serializer for refunds."""

    class Meta:
        model = Refund
        fields = [
            "id",
            "payment",
            "amount",
            "reason",
            "reason_details",
            "status",
            "external_refund_id",
            "completed_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "external_refund_id",
            "completed_at",
            "created_at",
        ]


class RefundCreateSerializer(serializers.Serializer):
    """Serializer for creating refunds."""

    payment_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    reason = serializers.ChoiceField(choices=Refund.REASON_CHOICES)
    reason_details = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_payment_id(self, value):
        restaurant = self.context.get("restaurant")
        try:
            payment = Payment.objects.get(
                id=value,
                order__restaurant=restaurant,
            )
            if not payment.is_refundable:
                raise serializers.ValidationError("Payment cannot be refunded.")
            return payment
        except Payment.DoesNotExist:
            raise serializers.ValidationError("Payment not found.")

    def validate(self, data):
        payment = data.get("payment_id")
        if payment and data["amount"] > payment.refundable_amount:
            raise serializers.ValidationError({"amount": f"Cannot refund more than {payment.refundable_amount}."})
        return data


class PaymentMethodSerializer(serializers.ModelSerializer):
    """Serializer for payment methods."""

    display_name = serializers.SerializerMethodField()

    class Meta:
        model = PaymentMethod
        fields = [
            "id",
            "method_type",
            "card_brand",
            "card_last4",
            "card_exp_month",
            "card_exp_year",
            "is_default",
            "is_active",
            "display_name",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "method_type",
            "card_brand",
            "card_last4",
            "card_exp_month",
            "card_exp_year",
            "created_at",
        ]

    def get_display_name(self, obj):
        return str(obj)


class PaymentMethodCreateSerializer(serializers.Serializer):
    """Serializer for adding payment methods."""

    payment_method_id = serializers.CharField(help_text="Stripe PaymentMethod ID (pm_xxx)")
    set_as_default = serializers.BooleanField(default=False)


class PaymentListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for payment lists."""

    order_number = serializers.CharField(source="order.order_number", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "order_number",
            "total_amount",
            "payment_method",
            "status",
            "receipt_number",
            "created_at",
        ]
