from __future__ import annotations

from rest_framework import serializers

from apps.ordering.models import Courier, Delivery, DeliveryZone, OnlineOrderingSettings, RestaurantDomain


class OnlineOrderingSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = OnlineOrderingSettings
        fields = [
            "pickup_enabled",
            "delivery_enabled",
            "asap_enabled",
            "scheduling_enabled",
            "lead_minutes",
            "delivery_extra_minutes",
            "slot_interval_minutes",
            "max_days_ahead",
            "cutoff_minutes_before_close",
            "min_order_pickup",
            "min_order_delivery",
            "free_delivery_over",
            "packaging_fee",
            "courier_provider",
            "auto_request_courier_on",
            "pass_platform_fee_to_guest",
            "paused_until",
            "pause_reason",
        ]
        read_only_fields = ["paused_until", "pause_reason"]


class DeliveryZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZone
        fields = [
            "id",
            "name",
            "kind",
            "radius_km",
            "polygon",
            "fee",
            "min_order",
            "eta_minutes",
            "is_active",
            "sort",
            "color",
        ]
        read_only_fields = ["id"]

    def validate(self, data):
        kind = data.get("kind", getattr(self.instance, "kind", "radius"))
        polygon = data.get("polygon", getattr(self.instance, "polygon", []))
        if kind == "polygon" and len(polygon or []) < 3:
            raise serializers.ValidationError({"polygon": "A polygon needs at least three points."})
        return data


class CourierSerializer(serializers.ModelSerializer):
    staff_user_id = serializers.SerializerMethodField()

    class Meta:
        model = Courier
        fields = ["id", "name", "phone", "vehicle", "is_active", "is_available", "staff", "staff_user_id"]
        read_only_fields = ["id", "staff_user_id"]

    def get_staff_user_id(self, obj):
        return str(obj.staff.user_id) if obj.staff_id else None


class DeliverySerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    order_id = serializers.UUIDField(source="order.id", read_only=True)
    order_status = serializers.CharField(source="order.status", read_only=True)
    customer_name = serializers.CharField(source="order.customer_name", read_only=True)
    customer_phone = serializers.CharField(source="order.customer_phone", read_only=True)
    address = serializers.CharField(source="order.delivery_address", read_only=True)
    address_json = serializers.JSONField(source="order.address_json", read_only=True)
    lat = serializers.DecimalField(source="order.delivery_lat", max_digits=9, decimal_places=6, read_only=True)
    lng = serializers.DecimalField(source="order.delivery_lng", max_digits=9, decimal_places=6, read_only=True)
    instructions = serializers.CharField(source="order.delivery_instructions", read_only=True)
    scheduled_for = serializers.DateTimeField(source="order.scheduled_for", read_only=True)
    total = serializers.DecimalField(source="order.total", max_digits=10, decimal_places=2, read_only=True)
    is_paid = serializers.SerializerMethodField()
    provider_display = serializers.CharField(source="get_provider_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    courier_id = serializers.UUIDField(source="courier.id", read_only=True, allow_null=True)

    class Meta:
        model = Delivery
        fields = [
            "id",
            "order_id",
            "order_number",
            "order_status",
            "provider",
            "provider_display",
            "status",
            "status_display",
            "courier_id",
            "courier_name",
            "courier_phone",
            "external_id",
            "tracking_url",
            "quote",
            "cost",
            "fee_charged",
            "pickup_eta",
            "dropoff_eta",
            "courier_lat",
            "courier_lng",
            "error",
            "requested_at",
            "picked_up_at",
            "delivered_at",
            "customer_name",
            "customer_phone",
            "address",
            "address_json",
            "lat",
            "lng",
            "instructions",
            "scheduled_for",
            "total",
            "is_paid",
            "events",
            "created_at",
        ]
        read_only_fields = fields

    def get_is_paid(self, obj):
        try:
            from apps.payments import services as ledger

            return ledger.is_paid(obj.order)
        except Exception:  # noqa: BLE001
            return obj.order.status not in ("pending_payment",)


class RequestCourierSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=["own", "wolt_drive", "glovo_odr"], required=False)


class AssignCourierSerializer(serializers.Serializer):
    courier_id = serializers.UUIDField()


class CourierUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["assigned", "picked_up", "delivered", "failed", "cancelled"])
    lat = serializers.FloatField(required=False, allow_null=True)
    lng = serializers.FloatField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class CancelCourierSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class PauseSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(min_value=1, max_value=24 * 60, default=30)
    reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=120)


class DeliveryQuoteRequestSerializer(serializers.Serializer):
    lat = serializers.FloatField()
    lng = serializers.FloatField()
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default=0)


class RestaurantDomainSerializer(serializers.ModelSerializer):
    is_verified = serializers.BooleanField(read_only=True)

    class Meta:
        model = RestaurantDomain
        fields = ["id", "domain", "is_primary", "verified_at", "last_check_at", "error", "is_verified", "created_at"]
        read_only_fields = ["id", "verified_at", "last_check_at", "error", "is_verified", "created_at"]

    def validate_domain(self, value):
        import re

        v = (value or "").strip().lower().rstrip(".")
        if not re.fullmatch(r"(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", v):
            raise serializers.ValidationError("Enter a valid domain name, e.g. order.myrestaurant.ge.")
        for blocked in ("aimenu.ge",):
            if v == blocked or v.endswith("." + blocked):
                raise serializers.ValidationError("Subdomains of aimenu.ge are managed automatically.")
        return v


class SummarySerializer(serializers.Serializer):
    pickup_today = serializers.IntegerField()
    delivery_today = serializers.IntegerField()
    in_flight = serializers.IntegerField()
    failed = serializers.IntegerField()
    unverified_domains = serializers.IntegerField()
    paused = serializers.BooleanField()
