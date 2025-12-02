"""
Serializers for tables app.
"""

from rest_framework import serializers

from .models import Table, TableQRCode, TableSection, TableSession


class TableSectionSerializer(serializers.ModelSerializer):
    """Serializer for table sections."""

    tables_count = serializers.SerializerMethodField()

    class Meta:
        model = TableSection
        fields = [
            "id",
            "name",
            "description",
            "display_order",
            "is_active",
            "tables_count",
        ]
        read_only_fields = ["id"]

    def get_tables_count(self, obj):
        return obj.tables.filter(is_active=True).count()


class TableQRCodeSerializer(serializers.ModelSerializer):
    """Serializer for QR codes."""

    class Meta:
        model = TableQRCode
        fields = [
            "id",
            "code",
            "name",
            "is_active",
            "scans_count",
            "last_scanned_at",
        ]
        read_only_fields = ["id", "code", "scans_count", "last_scanned_at"]


class TableSerializer(serializers.ModelSerializer):
    """Serializer for tables."""

    section_name = serializers.CharField(source="section.name", read_only=True)
    qr_codes = TableQRCodeSerializer(many=True, read_only=True)
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = Table
        fields = [
            "id",
            "number",
            "name",
            "display_name",
            "capacity",
            "min_capacity",
            "status",
            "is_active",
            "section",
            "section_name",
            "position_x",
            "position_y",
            "shape",
            "qr_codes",
        ]
        read_only_fields = ["id", "display_name"]


class TableCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating tables."""

    generate_qr = serializers.BooleanField(default=True, write_only=True)

    class Meta:
        model = Table
        fields = [
            "number",
            "name",
            "capacity",
            "min_capacity",
            "section",
            "position_x",
            "position_y",
            "shape",
            "generate_qr",
        ]

    def validate_section(self, value):
        if value and value.restaurant != self.context.get("restaurant"):
            raise serializers.ValidationError("Section must belong to the same restaurant.")
        return value

    def create(self, validated_data):
        generate_qr = validated_data.pop("generate_qr", True)
        restaurant = self.context.get("restaurant")
        validated_data["restaurant"] = restaurant

        table = Table.objects.create(**validated_data)

        if generate_qr:
            TableQRCode.objects.create(table=table)

        return table


class TableSessionSerializer(serializers.ModelSerializer):
    """Serializer for table sessions."""

    table_number = serializers.CharField(source="table.number", read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)

    class Meta:
        model = TableSession
        fields = [
            "id",
            "table",
            "table_number",
            "guest_count",
            "status",
            "started_at",
            "closed_at",
            "notes",
            "duration_minutes",
        ]
        read_only_fields = ["id", "started_at", "closed_at", "duration_minutes"]


class TableSessionCreateSerializer(serializers.Serializer):
    """Serializer for starting a table session."""

    qr_code = serializers.CharField(required=False)
    table_id = serializers.UUIDField(required=False)
    guest_count = serializers.IntegerField(min_value=1, default=1)

    def validate(self, data):
        if not data.get("qr_code") and not data.get("table_id"):
            raise serializers.ValidationError("Either qr_code or table_id is required.")
        return data


class QRCodeScanSerializer(serializers.Serializer):
    """Serializer for QR code scan."""

    code = serializers.CharField()

    def validate_code(self, value):
        table = TableQRCode.get_table_by_code(value)
        if not table:
            raise serializers.ValidationError("Invalid or inactive QR code.")
        return value


class QRCodeScanResponseSerializer(serializers.Serializer):
    """Response serializer for QR scan."""

    restaurant_name = serializers.CharField()
    restaurant_slug = serializers.CharField()
    table_number = serializers.CharField()
    table_name = serializers.CharField()
