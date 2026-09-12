from rest_framework import serializers


class PlatformStatusSerializer(serializers.Serializer):
    platform = serializers.CharField()
    label = serializers.CharField()
    implemented = serializers.BooleanField()
    is_enabled = serializers.BooleanField()
    configured = serializers.BooleanField()
    store_external_id = serializers.CharField(allow_blank=True)
    auto_accept = serializers.BooleanField()
    prep_time_minutes = serializers.IntegerField()
    sandbox = serializers.BooleanField()
    online = serializers.BooleanField()
    paused_until = serializers.DateTimeField(allow_null=True)
    last_menu_sync_at = serializers.DateTimeField(allow_null=True)
    last_menu_sync_status = serializers.CharField(allow_blank=True)
    orders_today = serializers.IntegerField()
    awaiting_accept = serializers.IntegerField()


class PauseSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(min_value=5, max_value=1440, default=30)


class StoreStatusSerializer(serializers.Serializer):
    online = serializers.BooleanField()
    paused_until = serializers.DateTimeField(allow_null=True)
    live = serializers.JSONField(allow_null=True)


class MenuSyncSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    kind = serializers.CharField()
    product_count = serializers.IntegerField()
    error = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    finished_at = serializers.DateTimeField(allow_null=True)


class MenuSyncRequestSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["full", "updates"], default="full")
