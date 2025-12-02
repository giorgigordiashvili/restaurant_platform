"""
Serializers for orders app.
"""

from rest_framework import serializers

from apps.menu.models import MenuItem, Modifier

from .models import Order, OrderItem, OrderItemModifier, OrderStatusHistory


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
            "status",
            "preparation_station",
            "special_instructions",
            "modifiers",
        ]
        read_only_fields = ["id", "item_name", "item_description", "unit_price", "total_price"]


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
            item = MenuItem.objects.get(
                id=value,
                restaurant=restaurant,
                is_available=True,
            )
            return item
        except MenuItem.DoesNotExist:
            raise serializers.ValidationError("Menu item not found or unavailable.")

    def validate_modifier_ids(self, value):
        if not value:
            return []
        modifiers = Modifier.objects.filter(id__in=value, is_available=True)
        if len(modifiers) != len(value):
            raise serializers.ValidationError("One or more modifiers not found or unavailable.")
        return list(modifiers)


class OrderSerializer(serializers.ModelSerializer):
    """Serializer for orders."""

    items = OrderItemSerializer(many=True, read_only=True)
    table_number = serializers.CharField(source="table.number", read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "order_type",
            "status",
            "table",
            "table_number",
            "customer_name",
            "customer_phone",
            "customer_email",
            "customer_notes",
            "delivery_address",
            "subtotal",
            "tax_amount",
            "service_charge",
            "discount_amount",
            "total",
            "estimated_ready_at",
            "confirmed_at",
            "completed_at",
            "cancelled_at",
            "cancellation_reason",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "order_number",
            "subtotal",
            "tax_amount",
            "service_charge",
            "total",
            "confirmed_at",
            "completed_at",
            "cancelled_at",
            "created_at",
            "updated_at",
        ]


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
            "table_number",
            "customer_name",
            "total",
            "items_count",
            "created_at",
        ]

    def get_items_count(self, obj):
        return obj.items.count()


class KitchenOrderSerializer(serializers.ModelSerializer):
    """Serializer for kitchen display."""

    items = serializers.SerializerMethodField()
    table_number = serializers.CharField(source="table.number", read_only=True)
    elapsed_minutes = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "order_type",
            "status",
            "table_number",
            "customer_notes",
            "items",
            "elapsed_minutes",
            "created_at",
        ]

    def get_items(self, obj):
        # Only show kitchen items
        items = obj.items.filter(preparation_station__in=["kitchen", "both"])
        return OrderItemSerializer(items, many=True).data

    def get_elapsed_minutes(self, obj):
        from django.utils import timezone

        delta = timezone.now() - obj.created_at
        return int(delta.total_seconds() / 60)
