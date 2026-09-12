from rest_framework import serializers

from apps.timekeeping.models import RotaShift, TimeEntry


class TimeEntrySerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    worked_minutes = serializers.IntegerField(read_only=True)

    class Meta:
        model = TimeEntry
        fields = [
            "id",
            "staff_member",
            "name",
            "role",
            "clock_in",
            "clock_out",
            "break_minutes",
            "source",
            "note",
            "auto_closed",
            "worked_minutes",
        ]

    def get_name(self, obj):
        u = obj.staff_member.user
        return u.get_full_name() or u.email

    def get_role(self, obj):
        return obj.staff_member.role.get_display_name() if obj.staff_member.role_id else ""


class ClockStatusSerializer(serializers.Serializer):
    clocked_in = serializers.BooleanField()
    entry = TimeEntrySerializer(allow_null=True)
    today_minutes = serializers.IntegerField()
    manager = serializers.BooleanField()


class ClockActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["in", "out"])
    break_minutes = serializers.IntegerField(min_value=0, max_value=600, required=False, default=0)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class RotaShiftSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    hours = serializers.DecimalField(max_digits=6, decimal_places=2, read_only=True)

    class Meta:
        model = RotaShift
        fields = ["id", "staff_member", "name", "date", "start_time", "end_time", "label", "note", "published", "hours"]

    def get_name(self, obj):
        u = obj.staff_member.user
        return u.get_full_name() or u.email
