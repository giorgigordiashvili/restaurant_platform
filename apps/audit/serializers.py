"""
Serializers for audit app.
"""

from rest_framework import serializers

from .models import AuditLog


class AuditLogListSerializer(serializers.ModelSerializer):
    """Serializer for listing audit logs."""

    user_email = serializers.EmailField(read_only=True)
    action_display = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "user_email",
            "action",
            "action_display",
            "target_model",
            "target_id",
            "description",
            "ip_address",
            "created_at",
        ]


class AuditLogDetailSerializer(serializers.ModelSerializer):
    """Serializer for audit log detail."""

    user_email = serializers.EmailField(read_only=True)
    action_display = serializers.CharField(source="get_action_display", read_only=True)
    restaurant_name = serializers.CharField(source="restaurant.name", read_only=True, allow_null=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "user",
            "user_email",
            "ip_address",
            "user_agent",
            "restaurant",
            "restaurant_name",
            "action",
            "action_display",
            "target_model",
            "target_id",
            "description",
            "changes",
            "request_method",
            "request_path",
            "response_status",
            "created_at",
        ]


class AuditLogStatsSerializer(serializers.Serializer):
    """Serializer for audit log statistics."""

    total_logs = serializers.IntegerField()
    logs_today = serializers.IntegerField()
    logs_this_week = serializers.IntegerField()
    logs_by_action = serializers.DictField(child=serializers.IntegerField())
    recent_logins = serializers.IntegerField()
    recent_failed_logins = serializers.IntegerField()
