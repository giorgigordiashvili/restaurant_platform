from __future__ import annotations

from rest_framework import serializers

from apps.waitlist.models import WaitlistEntry, WaitlistSettings


class WaitlistSettingsSerializer(serializers.ModelSerializer):
    join_url = serializers.SerializerMethodField()

    class Meta:
        model = WaitlistSettings
        fields = [
            "default_wait_minutes",
            "notify_expire_minutes",
            "allow_self_join",
            "max_party_size",
            "sms_on_join",
            "sms_on_ready",
            "join_url",
        ]

    def get_join_url(self, obj):
        from apps.waitlist import services

        return services.join_url(obj.restaurant)


class WaitlistEntrySerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    waited_minutes = serializers.IntegerField(read_only=True)
    table_number = serializers.SerializerMethodField()
    reservation_code = serializers.SerializerMethodField()
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = WaitlistEntry
        fields = [
            "id",
            "date",
            "position",
            "name",
            "phone",
            "party_size",
            "quoted_minutes",
            "status",
            "status_display",
            "is_open",
            "source",
            "notes",
            "notified_at",
            "notify_count",
            "seated_at",
            "left_at",
            "table",
            "table_number",
            "session",
            "reservation",
            "reservation_code",
            "estimated_ready_at",
            "waited_minutes",
            "created_at",
        ]
        read_only_fields = fields

    def get_table_number(self, obj):
        return obj.table.number if obj.table_id else ""

    def get_reservation_code(self, obj):
        return obj.reservation.confirmation_code if obj.reservation_id else ""


class AddEntrySerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    party_size = serializers.IntegerField(min_value=1, max_value=50, default=2)
    quoted_minutes = serializers.IntegerField(min_value=0, max_value=300, required=False, allow_null=True)
    notes = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class UpdateEntrySerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120, required=False)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    party_size = serializers.IntegerField(min_value=1, max_value=50, required=False)
    quoted_minutes = serializers.IntegerField(min_value=0, max_value=300, required=False)
    notes = serializers.CharField(max_length=200, required=False, allow_blank=True)
    position = serializers.IntegerField(min_value=1, required=False)


class SeatSerializer(serializers.Serializer):
    table_id = serializers.UUIDField()


class JoinSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    phone = serializers.CharField(max_length=20)
    party_size = serializers.IntegerField(min_value=1, max_value=50, default=2)


class PublicStatusSerializer(serializers.Serializer):
    name = serializers.CharField()
    party_size = serializers.IntegerField()
    status = serializers.CharField()
    position = serializers.IntegerField()
    ahead = serializers.IntegerField()
    quoted_minutes = serializers.IntegerField()
    estimated_ready_at = serializers.DateTimeField(allow_null=True)
    restaurant = serializers.CharField()
    restaurant_slug = serializers.CharField()


class SummarySerializer(serializers.Serializer):
    waiting = serializers.IntegerField()
    notified = serializers.IntegerField()
    longest_wait = serializers.IntegerField()
    seated_today = serializers.IntegerField()
