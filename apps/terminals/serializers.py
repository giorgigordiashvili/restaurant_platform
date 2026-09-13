from __future__ import annotations

from rest_framework import serializers

from apps.terminals.models import PaymentTerminal, TerminalTransaction


class TerminalSerializer(serializers.ModelSerializer):
    provider_display = serializers.CharField(source="get_provider_display", read_only=True)
    is_online = serializers.BooleanField(read_only=True)
    is_link = serializers.BooleanField(read_only=True)
    configured = serializers.SerializerMethodField()

    class Meta:
        model = PaymentTerminal
        fields = [
            "id",
            "name",
            "provider",
            "provider_display",
            "ecr_protocol",
            "terminal_id",
            "is_active",
            "is_default",
            "is_online",
            "is_link",
            "auto_receipt",
            "timeout_seconds",
            "configured",
            "last_seen_at",
            "last_error",
        ]
        read_only_fields = fields

    def get_configured(self, obj):
        from apps.terminals.providers import registry

        return registry.is_configured(obj)


class TransactionSerializer(serializers.ModelSerializer):
    terminal_name = serializers.CharField(source="terminal.name", read_only=True)
    provider = serializers.CharField(source="terminal.provider", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    order_number = serializers.SerializerMethodField()
    payment_id = serializers.UUIDField(source="payment.id", read_only=True, allow_null=True)
    receipt_number = serializers.SerializerMethodField()
    total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = TerminalTransaction
        fields = [
            "id",
            "terminal",
            "terminal_name",
            "provider",
            "kind",
            "amount",
            "tip",
            "total",
            "currency",
            "status",
            "status_display",
            "is_open",
            "order",
            "order_number",
            "session",
            "order_ids",
            "payment_id",
            "receipt_number",
            "external_id",
            "auth_code",
            "card_mask",
            "rrn",
            "pay_url",
            "error",
            "sent_to",
            "expires_at",
            "finished_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_order_number(self, obj):
        return obj.order.order_number if obj.order_id else ""

    def get_receipt_number(self, obj):
        return obj.payment.receipt_number if obj.payment_id else ""


class StartSaleSerializer(serializers.Serializer):
    terminal_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    tip_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, default=0)
    order_id = serializers.UUIDField(required=False)
    order_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    session_id = serializers.UUIDField(required=False)
    send_to = serializers.CharField(max_length=254, required=False, allow_blank=True, default="")

    def validate(self, data):
        targets = [k for k in ("order_id", "order_ids", "session_id") if data.get(k)]
        if len(targets) != 1:
            raise serializers.ValidationError("Give exactly one of order_id, order_ids or session_id.")
        return data


class ConfirmSerializer(serializers.Serializer):
    auth_code = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    card_mask = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")


class DeclineSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class SendLinkSerializer(serializers.Serializer):
    to = serializers.CharField(max_length=254)


class RefundSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0.01)
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class BridgeResultSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["approved", "declined", "cancelled", "failed"])
    auth_code = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    card_mask = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    rrn = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    error = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    raw = serializers.DictField(required=False, default=dict)


class BridgeJobSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    amount = serializers.CharField()
    currency = serializers.CharField()
    protocol = serializers.CharField()
    device = serializers.DictField()
    reference = serializers.CharField()
    rrn = serializers.CharField()


class SummarySerializer(serializers.Serializer):
    awaiting = serializers.IntegerField()
    declined_today = serializers.IntegerField()
    offline_bridges = serializers.IntegerField()
    terminals = serializers.IntegerField()
