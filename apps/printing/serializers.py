from rest_framework import serializers

from .models import Printer, PrintJob


class PrinterSerializer(serializers.ModelSerializer):
    is_online = serializers.BooleanField(read_only=True)
    queued_jobs = serializers.SerializerMethodField()

    class Meta:
        model = Printer
        fields = [
            "id",
            "name",
            "kind",
            "stations",
            "paper",
            "connection",
            "copies",
            "auto_print",
            "open_drawer",
            "is_active",
            "is_online",
            "last_seen_at",
            "last_error",
            "queued_jobs",
            "created_at",
        ]
        read_only_fields = ["id", "is_online", "last_seen_at", "last_error", "queued_jobs", "created_at"]

    def get_queued_jobs(self, obj):
        return obj.jobs.filter(status="queued").count()


class PrinterSetupSerializer(PrinterSerializer):
    """Adds the bridge key -- only for settings editors (create / rotate responses)."""

    class Meta(PrinterSerializer.Meta):
        fields = PrinterSerializer.Meta.fields + ["bridge_key"]
        read_only_fields = PrinterSerializer.Meta.read_only_fields + ["bridge_key"]


class PrintJobSerializer(serializers.ModelSerializer):
    printer_name = serializers.CharField(source="printer.name", read_only=True)
    order_number = serializers.CharField(source="order.order_number", read_only=True, default="")

    class Meta:
        model = PrintJob
        fields = [
            "id",
            "printer",
            "printer_name",
            "kind",
            "title",
            "order",
            "order_number",
            "payment",
            "status",
            "attempts",
            "error",
            "requested_by",
            "claimed_at",
            "printed_at",
            "created_at",
        ]
        read_only_fields = fields


class PrintJobCreateSerializer(serializers.Serializer):
    """Ask for a ticket / receipt / report to be printed (or re-printed)."""

    kind = serializers.ChoiceField(choices=[("ticket", "ticket"), ("receipt", "receipt"), ("report", "report")])
    order_id = serializers.UUIDField(required=False)
    payment_id = serializers.UUIDField(required=False)
    shift_id = serializers.UUIDField(required=False)
    printer_id = serializers.UUIDField(required=False, help_text="Default: every matching active printer.")
    station = serializers.ChoiceField(choices=[("kitchen", "kitchen"), ("bar", "bar")], required=False)

    def validate(self, data):
        kind = data["kind"]
        if kind in ("ticket", "receipt") and not data.get("order_id") and not data.get("payment_id"):
            raise serializers.ValidationError("order_id is required.")
        if kind == "report" and not data.get("shift_id"):
            raise serializers.ValidationError("shift_id is required.")
        return data


class BridgeJobSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    title = serializers.CharField()
    copies = serializers.IntegerField()
    escpos_b64 = serializers.CharField()


class BridgeFailSerializer(serializers.Serializer):
    error = serializers.CharField(required=False, allow_blank=True, default="")
