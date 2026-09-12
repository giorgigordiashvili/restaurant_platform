"""
Serializers for payments app.
"""

from rest_framework import serializers

from .models import CashMovement, CashShift, DiscountReason, Payment, PaymentAllocation, PaymentMethod, Refund


class PaymentAllocationSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)

    class Meta:
        model = PaymentAllocation
        fields = ["order", "order_number", "amount"]


class PaymentSerializer(serializers.ModelSerializer):
    """Serializer for payments."""

    order_number = serializers.CharField(source="order.order_number", read_only=True)
    allocations = PaymentAllocationSerializer(many=True, read_only=True)
    processed_by_name = serializers.SerializerMethodField()
    refunded_amount = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "id",
            "order",
            "order_number",
            "session",
            "shift",
            "amount",
            "tip_amount",
            "total_amount",
            "tendered",
            "change_given",
            "payment_method",
            "status",
            "currency",
            "receipt_number",
            "external_payment_id",
            "processed_by",
            "processed_by_name",
            "refunded_amount",
            "allocations",
            "notes",
            "completed_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_processed_by_name(self, obj):
        u = obj.processed_by
        return (u.get_full_name() or u.email) if u else ""

    def get_refunded_amount(self, obj):
        return str(obj.refunded_amount)


class CashMovementSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = CashMovement
        fields = ["id", "kind", "amount", "reason", "created_by", "created_by_name", "created_at"]
        read_only_fields = fields

    def get_created_by_name(self, obj):
        u = obj.created_by
        return (u.get_full_name() or u.email) if u else ""


class CashShiftSerializer(serializers.ModelSerializer):
    opened_by_name = serializers.SerializerMethodField()
    closed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = CashShift
        fields = [
            "id",
            "number",
            "register",
            "status",
            "opened_by",
            "opened_by_name",
            "opened_at",
            "closed_by",
            "closed_by_name",
            "closed_at",
            "opening_float",
            "counted_cash",
            "expected_cash",
            "difference",
            "report",
            "notes",
        ]
        read_only_fields = fields

    def get_opened_by_name(self, obj):
        u = obj.opened_by
        return (u.get_full_name() or u.email) if u else ""

    def get_closed_by_name(self, obj):
        u = obj.closed_by
        return (u.get_full_name() or u.email) if u else ""


class DiscountReasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiscountReason
        fields = ["id", "kind", "label", "requires_manager", "is_active", "sort_order"]


class ReasonOptionSerializer(serializers.Serializer):
    """A reason the POS may offer: a stored one (id) or a built-in default (id=null)."""

    id = serializers.UUIDField(allow_null=True)
    label = serializers.CharField()
    kind = serializers.CharField()
    requires_manager = serializers.BooleanField()


class OpenShiftSerializer(serializers.Serializer):
    opening_float = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, default=0)
    register = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class CloseShiftSerializer(serializers.Serializer):
    counted_cash = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class CashMovementCreateSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=CashMovement.KIND_CHOICES)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    reason = serializers.CharField(max_length=200)


class RecordPaymentSerializer(serializers.Serializer):
    """
    Take a payment at the POS. Exactly one of order_id / order_ids / session_id.
    ``amount`` is what goes against the bill (a partial amount is fine);
    ``tendered`` is the cash handed over, from which change is worked out.
    """

    method = serializers.ChoiceField(choices=[(m, m) for m in Payment.STAFF_METHODS])
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    tip_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, default=0)
    tendered = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False, allow_null=True)
    order_id = serializers.UUIDField(required=False)
    order_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    session_id = serializers.UUIDField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, data):
        targets = [k for k in ("order_id", "order_ids", "session_id") if data.get(k)]
        if len(targets) != 1:
            raise serializers.ValidationError("Give exactly one of order_id, order_ids or session_id.")
        if data.get("method") == "cash" and data.get("tendered") is None:
            data["tendered"] = data["amount"] + data.get("tip_amount", 0)
        return data


class RecordPaymentResponseSerializer(serializers.Serializer):
    payment = PaymentSerializer()
    change = serializers.DecimalField(max_digits=10, decimal_places=2)
    receipt_number = serializers.CharField()
    balance = serializers.DecimalField(max_digits=10, decimal_places=2)
    paid_order_numbers = serializers.ListField(child=serializers.CharField())


class SplitEvenSerializer(serializers.Serializer):
    order_id = serializers.UUIDField(required=False)
    session_id = serializers.UUIDField(required=False)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False)
    ways = serializers.IntegerField(min_value=1, max_value=50)

    def validate(self, data):
        if not any(data.get(k) is not None for k in ("order_id", "session_id", "amount")):
            raise serializers.ValidationError("Give an order_id, a session_id or an amount to split.")
        return data


class SplitEvenResponseSerializer(serializers.Serializer):
    total = serializers.DecimalField(max_digits=10, decimal_places=2)
    ways = serializers.IntegerField()
    shares = serializers.ListField(child=serializers.DecimalField(max_digits=10, decimal_places=2))


class PaymentRefundSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    method = serializers.ChoiceField(choices=Refund.METHOD_CHOICES, required=False)
    reason = serializers.ChoiceField(choices=Refund.REASON_CHOICES, default="customer_request")
    reason_id = serializers.UUIDField(required=False, allow_null=True)
    reason_details = serializers.CharField(required=False, allow_blank=True, default="")
    order_id = serializers.UUIDField(required=False, allow_null=True)


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

    receipt_number = serializers.CharField(source="payment.receipt_number", read_only=True)

    class Meta:
        model = Refund
        fields = [
            "id",
            "payment",
            "receipt_number",
            "order",
            "shift",
            "amount",
            "method",
            "reason",
            "reason_code",
            "reason_details",
            "status",
            "external_refund_id",
            "processed_by",
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
                restaurant=restaurant,
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
            "order",
            "order_number",
            "session",
            "shift",
            "amount",
            "tip_amount",
            "total_amount",
            "change_given",
            "payment_method",
            "status",
            "receipt_number",
            "completed_at",
            "created_at",
        ]
