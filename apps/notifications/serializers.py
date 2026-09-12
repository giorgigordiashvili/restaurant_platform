from rest_framework import serializers

from apps.notifications import events
from apps.notifications.models import Device, Notification, OutboundMessage, StaffNotificationPrefs


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "event", "title", "body", "data", "url", "read_at", "created_at"]
        read_only_fields = fields


class UnreadCountSerializer(serializers.Serializer):
    unread = serializers.IntegerField()
    latest_id = serializers.UUIDField(allow_null=True)


class MarkReadSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.UUIDField(), required=False)


class DeviceRegisterSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=500)
    kind = serializers.ChoiceField(choices=[k for k, _ in Device.KIND_CHOICES], default="expo")
    platform = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    app_version = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = ["id", "kind", "platform", "app_version", "is_active", "last_seen_at", "last_error"]
        read_only_fields = fields


class EventSerializer(serializers.Serializer):
    code = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    group = serializers.CharField()
    muted = serializers.BooleanField()


class PrefsSerializer(serializers.ModelSerializer):
    events = serializers.SerializerMethodField()
    muted_events = serializers.ListField(child=serializers.CharField(), required=False)

    class Meta:
        model = StaffNotificationPrefs
        fields = ["push", "email", "quiet_from", "quiet_to", "muted_events", "events"]

    def validate_muted_events(self, value):
        bad = [v for v in value if v not in events.EVENTS_BY_CODE]
        if bad:
            raise serializers.ValidationError(f"Unknown events: {', '.join(bad)}")
        return value

    def get_events(self, obj):
        muted = set(obj.muted_events or [])
        return [
            {"code": e.code, "title": e.title, "description": e.description, "group": e.group, "muted": e.code in muted}
            for e in events.EVENTS
        ]


class OutboundMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutboundMessage
        fields = ["id", "channel", "to", "subject", "kind", "status", "provider", "error", "sent_at", "created_at"]
        read_only_fields = fields


class TestMessageSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=["sms", "email"])
    to = serializers.CharField(max_length=254)
