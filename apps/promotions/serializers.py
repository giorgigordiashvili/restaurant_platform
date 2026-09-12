from rest_framework import serializers

from apps.promotions.models import MenuSchedule, Promotion


class MenuScheduleSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()

    class Meta:
        model = MenuSchedule
        fields = ["id", "name", "weekdays", "start_time", "end_time", "is_active", "label"]

    def get_label(self, obj):
        return obj.label()


class PromotionSerializer(serializers.ModelSerializer):
    live_now = serializers.SerializerMethodField()
    schedule_label = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = [
            "id",
            "name",
            "description",
            "kind",
            "is_active",
            "live_now",
            "mode",
            "value",
            "applies_to",
            "code",
            "starts_on",
            "ends_on",
            "min_order_amount",
            "max_uses",
            "max_uses_per_customer",
            "uses_count",
            "channels",
            "stackable",
            "schedule_label",
        ]

    def get_live_now(self, obj):
        return obj.is_live(self.context.get("local_now"))

    def get_schedule_label(self, obj):
        return obj.schedule.label() if obj.schedule_id else ""


class SetUnavailableSerializer(serializers.Serializer):
    until = serializers.ChoiceField(choices=["today", "tomorrow", "clear"], default="today")


class ItemAvailabilitySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    available_now = serializers.BooleanField()
    available_from = serializers.CharField(allow_blank=True)
    unavailable_until = serializers.DateTimeField(allow_null=True)
    is_available = serializers.BooleanField()


class ValidateCodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=30)
    items = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    channel = serializers.ChoiceField(choices=["web", "qr", "pos"], default="web")


class ValidateCodeResultSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    code = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    mode = serializers.CharField(allow_blank=True)
    value = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    discount = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    error = serializers.CharField(allow_blank=True)
    error_code = serializers.CharField(allow_blank=True)
