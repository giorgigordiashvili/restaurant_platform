from rest_framework import serializers

from apps.crm.models import Campaign, Customer, Segment


class CustomerSerializer(serializers.ModelSerializer):
    days_since_visit = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            "phone",
            "email",
            "birthday",
            "language",
            "tags",
            "notes",
            "marketing_opt_in",
            "opt_in_at",
            "source",
            "first_seen_at",
            "last_visit_at",
            "last_order_at",
            "visits",
            "orders_count",
            "reservations_count",
            "reviews_count",
            "total_spend",
            "avg_ticket",
            "last_rating",
            "days_since_visit",
        ]
        read_only_fields = [f for f in fields if f not in ("name", "birthday", "tags", "notes")]


class ConsentSerializer(serializers.Serializer):
    marketing_opt_in = serializers.BooleanField()


class SegmentSerializer(serializers.ModelSerializer):
    count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Segment
        fields = ["id", "name", "description", "rules", "is_builtin", "is_active", "count"]


class CampaignSerializer(serializers.ModelSerializer):
    segment_name = serializers.CharField(source="segment.name", read_only=True)

    class Meta:
        model = Campaign
        fields = [
            "id",
            "name",
            "channel",
            "segment",
            "segment_name",
            "subject",
            "body",
            "promotion",
            "scheduled_at",
            "status",
            "audience_count",
            "sent_count",
            "failed_count",
            "skipped_count",
            "started_at",
            "finished_at",
        ]
        read_only_fields = [
            "status",
            "audience_count",
            "sent_count",
            "failed_count",
            "skipped_count",
            "started_at",
            "finished_at",
        ]


class SummarySerializer(serializers.Serializer):
    customers = serializers.IntegerField()
    opted_in = serializers.IntegerField()
    new_30d = serializers.IntegerField()
    returning_30d = serializers.IntegerField()
    campaigns_30d = serializers.IntegerField()
    messages_30d = serializers.IntegerField()
