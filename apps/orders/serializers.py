"""
Serializers for orders app.
"""

from rest_framework import serializers

from apps.menu.models import SELLABLE, MenuItem, Modifier

from .models import Order, OrderDiscount, OrderItem, OrderItemModifier, OrderStatusHistory


class OrderItemModifierSerializer(serializers.ModelSerializer):
    """Serializer for order item modifiers."""

    class Meta:
        model = OrderItemModifier
        fields = [
            "id",
            "modifier_name",
            "price_adjustment",
        ]
        read_only_fields = ["id"]


class OrderItemSerializer(serializers.ModelSerializer):
    """Serializer for order items."""

    modifiers = OrderItemModifierSerializer(many=True, read_only=True)

    net_price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    discount_reason_label = serializers.SerializerMethodField()
    void_reason_label = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "menu_item",
            "item_name",
            "item_description",
            "unit_price",
            "quantity",
            "total_price",
            "discount_amount",
            "net_price",
            "is_comped",
            "discount_reason_label",
            "status",
            "voided_at",
            "void_reason_label",
            "was_sent_to_kitchen",
            "preparation_station",
            "special_instructions",
            "modifiers",
        ]
        read_only_fields = [
            "id",
            "item_name",
            "item_description",
            "unit_price",
            "total_price",
            "discount_amount",
            "net_price",
            "is_comped",
            "discount_reason_label",
            "voided_at",
            "void_reason_label",
            "was_sent_to_kitchen",
        ]

    def get_discount_reason_label(self, obj):
        if obj.discount_reason_id and obj.discount_reason:
            return obj.discount_reason.label
        return obj.discount_reason_text or ""

    def get_void_reason_label(self, obj):
        if obj.void_reason_id and obj.void_reason:
            return obj.void_reason.label
        return obj.void_reason_text or ""


class OrderDiscountSerializer(serializers.ModelSerializer):
    label = serializers.CharField(read_only=True)

    class Meta:
        model = OrderDiscount
        fields = ["id", "kind", "mode", "value", "amount", "label", "applied_by", "created_at"]
        read_only_fields = fields


class OrderPaymentBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    payment_method = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    tip_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    receipt_number = serializers.CharField()
    status = serializers.CharField()
    completed_at = serializers.DateTimeField(allow_null=True)


class OrderItemCreateSerializer(serializers.Serializer):
    """Serializer for adding items to an order."""

    menu_item_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, default=1)
    modifier_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    special_instructions = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_menu_item_id(self, value):
        restaurant = self.context.get("restaurant")
        try:
            item = (
                MenuItem.objects.filter(SELLABLE)
                .select_related("restaurant", "schedule", "category__schedule")
                .get(id=value, restaurant=restaurant)
            )
        except MenuItem.DoesNotExist:
            raise serializers.ValidationError("Menu item not found or unavailable.")
        from apps.promotions.availability import availability

        ok, reason = availability(item, restaurant=item.restaurant)
        if not ok:
            raise serializers.ValidationError(f"{item.safe_translation_getter('name', any_language=True)}: {reason}")
        return item

    def validate_modifier_ids(self, value):
        if not value:
            return []
        # Scoped to the restaurant: a combined venue menu makes other tenants'
        # modifier ids visible to honest clients, never mind hostile ones.
        modifiers = Modifier.objects.filter(SELLABLE, id__in=value, group__restaurant=self.context.get("restaurant"))
        if len(modifiers) != len(value):
            raise serializers.ValidationError("One or more modifiers not found or unavailable.")
        return list(modifiers)


class OrderSerializer(serializers.ModelSerializer):
    """Serializer for orders."""

    items = OrderItemSerializer(many=True, read_only=True)
    table_number = serializers.CharField(source="table.number", read_only=True)
    discounts = OrderDiscountSerializer(many=True, read_only=True)
    paid_amount = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()
    is_paid = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "order_type",
            "status",
            "table",
            "table_number",
            "table_session",
            "customer_name",
            "customer_phone",
            "customer_email",
            "customer_notes",
            "delivery_address",
            "subtotal",
            "tax_amount",
            "service_charge",
            "discount_amount",
            "discounts",
            "wallet_applied",
            "tip_amount",
            "server",
            "total",
            "paid_amount",
            "balance",
            "is_paid",
            "payments",
            "estimated_ready_at",
            "confirmed_at",
            "completed_at",
            "cancelled_at",
            "cancellation_reason",
            "source",
            "external_id",
            "platform_data",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "order_number",
            "source",
            "external_id",
            "platform_data",
            "subtotal",
            "tax_amount",
            "service_charge",
            "discount_amount",
            "wallet_applied",
            "total",
            "confirmed_at",
            "completed_at",
            "cancelled_at",
            "created_at",
            "updated_at",
        ]

    def _paid(self, obj):
        from apps.payments.services import paid_amount

        cache = self.context.setdefault("_paid_cache", {})
        if obj.pk not in cache:
            cache[obj.pk] = paid_amount(obj)
        return cache[obj.pk]

    def get_paid_amount(self, obj):
        return str(self._paid(obj))

    def get_balance(self, obj):
        from decimal import Decimal

        return str(max((obj.total or Decimal("0")) - self._paid(obj), Decimal("0")))

    def get_is_paid(self, obj):
        from decimal import Decimal

        return obj.status == "cancelled" or (obj.total or Decimal("0")) - self._paid(obj) <= 0

    def get_payments(self, obj):
        rows = []
        for a in obj.payment_allocations.select_related("payment").order_by("payment__created_at"):
            p = a.payment
            if p.status not in ("completed", "partially_refunded", "refunded"):
                continue
            rows.append(
                {
                    "id": str(p.pk),
                    "payment_method": p.payment_method,
                    "amount": str(a.amount),
                    "tip_amount": str(p.tip_amount),
                    "receipt_number": p.receipt_number,
                    "status": p.status,
                    "completed_at": p.completed_at.isoformat() if p.completed_at else None,
                }
            )
        return rows


class OrderCreateSerializer(serializers.Serializer):
    """Serializer for creating orders."""

    order_type = serializers.ChoiceField(choices=Order.ORDER_TYPE_CHOICES, default="dine_in")
    table_id = serializers.UUIDField(required=False)
    session_id = serializers.UUIDField(required=False)
    customer_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    customer_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    customer_email = serializers.EmailField(required=False, allow_blank=True)
    customer_notes = serializers.CharField(required=False, allow_blank=True)
    delivery_address = serializers.CharField(required=False, allow_blank=True)
    tip_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False, default=0)
    promo_code = serializers.CharField(max_length=30, required=False, allow_blank=True)
    marketing_opt_in = serializers.BooleanField(required=False, default=False)
    items = OrderItemCreateSerializer(many=True, min_length=1)

    def validate(self, data):
        order_type = data.get("order_type", "dine_in")

        # Dine-in orders require table
        if order_type == "dine_in" and not data.get("table_id") and not data.get("session_id"):
            raise serializers.ValidationError({"table_id": "Table or session is required for dine-in orders."})

        # Delivery orders require address
        if order_type == "delivery" and not data.get("delivery_address"):
            raise serializers.ValidationError({"delivery_address": "Delivery address is required for delivery orders."})

        return data


class OrderStatusUpdateSerializer(serializers.Serializer):
    """Serializer for updating order status."""

    status = serializers.ChoiceField(choices=Order.STATUS_CHOICES)
    notes = serializers.CharField(required=False, allow_blank=True)
    estimated_minutes = serializers.IntegerField(min_value=1, required=False)
    cancellation_reason = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        if data.get("status") == "cancelled" and not data.get("cancellation_reason"):
            raise serializers.ValidationError({"cancellation_reason": "Reason is required for cancellation."})
        return data


class ReasonMixin(serializers.Serializer):
    reason_id = serializers.UUIDField(required=False, allow_null=True)
    reason_text = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class PromoCodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=30)


class OrderDiscountCreateSerializer(ReasonMixin):
    mode = serializers.ChoiceField(choices=OrderDiscount.MODE_CHOICES, default="percent")
    value = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)


class OrderDiscountDeleteSerializer(serializers.Serializer):
    discount_id = serializers.UUIDField(required=False, allow_null=True)


class OrderItemDiscountSerializer(ReasonMixin):
    mode = serializers.ChoiceField(choices=OrderDiscount.MODE_CHOICES, default="percent")
    value = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)


class OrderItemReasonSerializer(ReasonMixin):
    """Comp / void: just a reason."""


class OrderSplitSerializer(serializers.Serializer):
    item_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1)
    table_id = serializers.UUIDField(required=False, allow_null=True)
    session_id = serializers.UUIDField(required=False, allow_null=True)


class OrderMoveSerializer(serializers.Serializer):
    table_id = serializers.UUIDField()


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    """Serializer for order status history."""

    changed_by_email = serializers.EmailField(source="changed_by.email", read_only=True)

    class Meta:
        model = OrderStatusHistory
        fields = [
            "id",
            "from_status",
            "to_status",
            "changed_by",
            "changed_by_email",
            "notes",
            "created_at",
        ]
        read_only_fields = ["id", "from_status", "to_status", "changed_by", "created_at"]


class OrderListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for order lists."""

    table_number = serializers.CharField(source="table.number", read_only=True)
    items_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "order_type",
            "status",
            "table",
            "table_number",
            "table_session",
            "customer_name",
            "source",
            "subtotal",
            "discount_amount",
            "total",
            "tip_amount",
            "server",
            "items_count",
            "created_at",
        ]

    def get_items_count(self, obj):
        return sum(1 for i in obj.items.all() if i.status != "cancelled")


class KitchenOrderSerializer(serializers.ModelSerializer):
    """
    One kitchen ticket. Every station's items are included (the POS kitchen
    screen is shared by kitchen and bar); the client tags bar items itself.
    """

    items = serializers.SerializerMethodField()
    table_number = serializers.CharField(source="table.number", read_only=True)
    elapsed_minutes = serializers.SerializerMethodField()
    platform_order_code = serializers.SerializerMethodField()
    pickup_eta = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "order_type",
            "status",
            "source",
            "external_id",
            "platform_order_code",
            "pickup_eta",
            "table_number",
            "customer_name",
            "customer_notes",
            "items",
            "elapsed_minutes",
            "confirmed_at",
            "estimated_ready_at",
            "created_at",
        ]

    def get_platform_order_code(self, obj):
        return (obj.platform_data or {}).get("order_code", "") if obj.source in ("glovo", "wolt", "bolt_food") else ""

    def get_pickup_eta(self, obj):
        return (obj.platform_data or {}).get("pickup_eta") if obj.source in ("glovo", "wolt", "bolt_food") else None

    def get_items(self, obj):
        # Walk the prefetch instead of re-querying per ticket.
        items = [i for i in obj.items.all() if i.status != "cancelled"]
        return OrderItemSerializer(items, many=True).data

    def get_elapsed_minutes(self, obj):
        from django.utils import timezone

        delta = timezone.now() - (obj.confirmed_at or obj.created_at)
        return max(int(delta.total_seconds() / 60), 0)
