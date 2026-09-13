from __future__ import annotations

from rest_framework import serializers

from apps.houseaccounts.models import HouseAccount, HouseAccountEntry, HouseAccountStatement


class HouseAccountSerializer(serializers.ModelSerializer):
    available = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True, allow_null=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    customer_name = serializers.SerializerMethodField()

    class Meta:
        model = HouseAccount
        fields = [
            "id",
            "name",
            "company",
            "tax_id",
            "phone",
            "email",
            "credit_limit",
            "balance",
            "available",
            "status",
            "status_display",
            "billing_day",
            "authorised_names",
            "require_signature",
            "notes",
            "customer",
            "customer_name",
            "last_payment_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "balance",
            "available",
            "status_display",
            "customer_name",
            "last_payment_at",
            "created_at",
        ]

    def get_customer_name(self, obj):
        return obj.customer.name if obj.customer_id else ""

    def validate_phone(self, value):
        from apps.notifications.providers import normalize_phone

        return normalize_phone(value) if value else ""


class EntrySerializer(serializers.ModelSerializer):
    order_number = serializers.SerializerMethodField()
    receipt_number = serializers.SerializerMethodField()
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = HouseAccountEntry
        fields = [
            "id",
            "kind",
            "kind_display",
            "amount",
            "balance_after",
            "order_number",
            "receipt_number",
            "signed_by",
            "note",
            "created_at",
        ]
        read_only_fields = fields

    def get_order_number(self, obj):
        return obj.order.order_number if obj.order_id else ""

    def get_receipt_number(self, obj):
        return obj.payment.receipt_number if obj.payment_id else ""


class StatementSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = HouseAccountStatement
        fields = [
            "id",
            "period_start",
            "period_end",
            "opening",
            "charges",
            "payments",
            "adjustments",
            "closing",
            "sent_at",
            "sent_to",
            "url",
            "created_at",
        ]
        read_only_fields = fields

    def get_url(self, obj):
        from apps.houseaccounts import services

        return services.statement_url(obj)


class ChargeSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    order_id = serializers.UUIDField(required=False)
    order_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    session_id = serializers.UUIDField(required=False)
    signed_by = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")

    def validate(self, data):
        targets = [k for k in ("order_id", "order_ids", "session_id") if data.get(k)]
        if len(targets) != 1:
            raise serializers.ValidationError("Give exactly one of order_id, order_ids or session_id.")
        return data


class SettleSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    method = serializers.ChoiceField(choices=["cash", "card_terminal", "other"])
    tendered = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class AdjustSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    note = serializers.CharField(max_length=200)
    writeoff = serializers.BooleanField(default=False)


class StatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["active", "suspended", "closed"])


class GenerateStatementSerializer(serializers.Serializer):
    period_start = serializers.DateField(required=False)
    period_end = serializers.DateField(required=False)
    send = serializers.BooleanField(default=False)


class SummarySerializer(serializers.Serializer):
    accounts = serializers.IntegerField()
    outstanding = serializers.DecimalField(max_digits=12, decimal_places=2)
    overdue = serializers.IntegerField()
