from rest_framework import serializers

from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine, Supplier


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ["id", "name", "contact_name", "phone", "email", "lead_days", "payment_terms", "is_active"]


class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    stock_item_name = serializers.CharField(source="stock_item.name", read_only=True)
    unit_code = serializers.CharField(source="unit.code", read_only=True)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    outstanding = serializers.DecimalField(max_digits=14, decimal_places=4, read_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = [
            "id",
            "stock_item",
            "stock_item_name",
            "quantity",
            "unit",
            "unit_code",
            "unit_price",
            "received_qty",
            "line_total",
            "outstanding",
            "note",
        ]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default="")
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "number",
            "status",
            "supplier",
            "supplier_name",
            "expected_on",
            "subtotal",
            "notes",
            "reference",
            "sent_at",
            "received_at",
            "created_at",
            "lines",
        ]


class ReceiveLineSerializer(serializers.Serializer):
    line_id = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=4, min_value=0)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=4, required=False, allow_null=True)
    expiry_date = serializers.DateField(required=False, allow_null=True)


class ReceiveSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    lines = ReceiveLineSerializer(many=True)
