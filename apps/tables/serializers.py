"""
Serializers for tables app.
"""

from rest_framework import serializers

from .models import Table, TableQRCode, TableSection, TableSession, TableSessionGuest


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
    orders_summary = serializers.SerializerMethodField()

    class Meta:
        model = TableSession
        fields = [
            "id",
            "table",
            "table_number",
            "guest_count",
            "status",
            "payment_mode",
            "host",
            "started_at",
            "closed_at",
            "notes",
            "duration_minutes",
            "orders_summary",
        ]
        read_only_fields = ["id", "host", "started_at", "closed_at", "duration_minutes"]

    def get_orders_summary(self, obj):
        """
        Per-session orders roll-up for the POS "Tables" tab. Returns status
        counts + the grand total + the `all_terminal` flag staff use to
        decide whether "Close table" is safe.
        """
        orders = list(obj.orders.prefetch_related("bog_transactions", "settle_transactions").all())
        counts = {
            "pending_payment": 0,
            "pending": 0,
            "confirmed": 0,
            "preparing": 0,
            "ready": 0,
            "served": 0,
            "completed": 0,
            "cancelled": 0,
        }
        grand_total = 0
        unpaid_numbers: list[str] = []
        unpaid_total = 0
        for o in orders:
            counts[o.status] = counts.get(o.status, 0) + 1
            if o.total is not None:
                grand_total += o.total
            if (
                o.status != "cancelled"
                and not any(t.status == "completed" for t in o.bog_transactions.all())
                and not any(t.status == "completed" for t in o.settle_transactions.all())
            ):
                unpaid_numbers.append(o.order_number)
                if o.total is not None:
                    unpaid_total += o.total
        non_terminal = sum(counts[s] for s in ("pending_payment", "pending", "confirmed", "preparing", "ready"))
        return {
            "counts": counts,
            "total_orders": len(orders),
            "non_terminal": non_terminal,
            "grand_total": str(grand_total),
            "all_terminal": non_terminal == 0 and len(orders) > 0,
            "unpaid_count": len(unpaid_numbers),
            "unpaid_order_numbers": unpaid_numbers,
            "unpaid_total": str(unpaid_total),
            "all_paid": len(unpaid_numbers) == 0,
        }


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


# ============== Session Guest Serializers ==============


class TableSessionGuestSerializer(serializers.ModelSerializer):
    """Serializer for session guests."""

    user_email = serializers.EmailField(source="user.email", read_only=True, default=None)
    display_name = serializers.CharField(read_only=True)
    order_count = serializers.SerializerMethodField()

    class Meta:
        model = TableSessionGuest
        fields = [
            "id",
            "user",
            "user_email",
            "guest_name",
            "guest_contact",
            "display_name",
            "is_host",
            "status",
            "joined_at",
            "left_at",
            "order_count",
        ]
        read_only_fields = ["id", "user", "user_email", "is_host", "joined_at", "left_at"]

    def get_order_count(self, obj):
        return obj.orders.count()


class TableSessionDetailSerializer(serializers.ModelSerializer):
    """Session details with guests and their orders."""

    table = TableSerializer(read_only=True)
    guests = TableSessionGuestSerializer(many=True, read_only=True)
    host_email = serializers.EmailField(source="host.email", read_only=True, default=None)
    invite_code = serializers.CharField(read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)
    restaurant_name = serializers.CharField(source="table.restaurant.name", read_only=True)
    restaurant_slug = serializers.CharField(source="table.restaurant.slug", read_only=True)
    orders_summary = serializers.SerializerMethodField()

    class Meta:
        model = TableSession
        fields = [
            "id",
            "table",
            "host",
            "host_email",
            "invite_code",
            "guest_count",
            "status",
            "payment_mode",
            "started_at",
            "closed_at",
            "duration_minutes",
            "guests",
            "restaurant_name",
            "restaurant_slug",
            "orders_summary",
        ]
        read_only_fields = fields

    def get_orders_summary(self, obj):
        # Reuse the dashboard serializer's method so the shape stays identical.
        return TableSessionSerializer().get_orders_summary(obj)


class JoinSessionSerializer(serializers.Serializer):
    """Serializer for joining a session."""

    guest_name = serializers.CharField(max_length=100, allow_blank=False, required=True)
    guest_contact = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")

    def validate_guest_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Guest name is required.")
        return value.strip()

    def validate_guest_contact(self, value):
        return (value or "").strip()


class SessionInviteResponseSerializer(serializers.Serializer):
    """Response serializer for session invite info."""

    invite_code = serializers.CharField()
    invite_url = serializers.CharField()
    session_id = serializers.UUIDField()
    table_number = serializers.CharField()
    restaurant_name = serializers.CharField()


class SessionJoinPreviewSerializer(serializers.Serializer):
    """Preview info before joining a session."""

    session_id = serializers.UUIDField()
    restaurant_name = serializers.CharField()
    restaurant_slug = serializers.CharField()
    table_number = serializers.CharField()
    table_name = serializers.CharField()
    host_name = serializers.CharField(allow_null=True)
    guest_count = serializers.IntegerField()
    status = serializers.CharField()
    started_at = serializers.CharField(required=False, allow_blank=True)
