"""
Serializers for shared venues.

Public cards deliberately expose only what the restaurant list already
exposes (plus branding). Nothing here ever nests an owner, email, IBAN,
session id or invite code.
"""

from rest_framework import serializers

from apps.tenants.serializers import RestaurantListSerializer

from .models import Venue, VenueSection, VenueShareRequest, VenueTable
from .services import LAYOUT_OURS, LAYOUT_THEIRS, layout_options

# ----------------------------------------------------------------------- public


class VenueRefSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()


class VenueCardSerializer(serializers.ModelSerializer):
    restaurants_count = serializers.SerializerMethodField()

    class Meta:
        model = Venue
        fields = ["id", "name", "slug", "description", "logo", "logo_blurhash", "restaurants_count"]

    def get_restaurants_count(self, obj):
        return obj.active_memberships().count()


class VenueRestaurantCardSerializer(RestaurantListSerializer):
    """A member restaurant as shown on the venue page."""

    display_order = serializers.SerializerMethodField()

    class Meta(RestaurantListSerializer.Meta):
        fields = [
            f
            for f in RestaurantListSerializer.Meta.fields
            if f not in ("city", "city_obj", "amenities", "accepts_reservations")
        ] + ["primary_color", "secondary_color", "default_currency", "display_order"]

    def get_display_order(self, obj):
        membership = getattr(obj, "venue_membership", None)
        return membership.display_order if membership else 0


class VenueTablePublicSerializer(serializers.ModelSerializer):
    section = serializers.CharField(source="section.name", read_only=True, default=None)

    class Meta:
        model = VenueTable
        fields = ["id", "number", "name", "capacity", "section", "code"]


class VenueMemberTableSerializer(serializers.Serializer):
    restaurant = VenueRestaurantCardSerializer()
    table_id = serializers.UUIDField(allow_null=True)
    table_code = serializers.CharField(allow_null=True)


class VenueDetailDataSerializer(serializers.Serializer):
    venue = VenueCardSerializer()
    restaurants = VenueRestaurantCardSerializer(many=True)


class VenueDetailResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = VenueDetailDataSerializer()


class VenueValidateDataSerializer(serializers.Serializer):
    venue = VenueCardSerializer()
    table = VenueTablePublicSerializer()
    restaurants = VenueMemberTableSerializer(many=True)


class VenueValidateResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = VenueValidateDataSerializer()


class VenueMenuEntrySerializer(serializers.Serializer):
    restaurant = VenueRestaurantCardSerializer()
    menu = serializers.DictField()


class VenueMenuDataSerializer(serializers.Serializer):
    venue = VenueCardSerializer()
    restaurants = VenueMenuEntrySerializer(many=True)


class VenueMenuResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = VenueMenuDataSerializer()


# ----------------------------------------------------------------------- dashboard


class RestaurantRefSerializer(serializers.Serializer):
    """The other party of a request: public identity only."""

    slug = serializers.CharField()
    name = serializers.CharField()
    logo = serializers.ImageField(allow_null=True)


class VenueShareRequestSerializer(serializers.ModelSerializer):
    from_restaurant = RestaurantRefSerializer(read_only=True)
    to_restaurant = RestaurantRefSerializer(read_only=True)
    direction = serializers.SerializerMethodField()
    layout_options = serializers.SerializerMethodField()

    class Meta:
        model = VenueShareRequest
        fields = [
            "id",
            "status",
            "direction",
            "from_restaurant",
            "to_restaurant",
            "venue_name",
            "message",
            "layout_options",
            "created_at",
            "expires_at",
            "responded_at",
        ]

    def get_direction(self, obj):
        me = self.context.get("restaurant")
        return "incoming" if me and obj.to_restaurant_id == me.pk else "outgoing"

    def get_layout_options(self, obj):
        return layout_options(obj) if obj.status == VenueShareRequest.STATUS_PENDING else []


class VenueShareRequestCreateSerializer(serializers.Serializer):
    to_restaurant = serializers.SlugField(help_text="Slug of the restaurant to share tables with")
    venue_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    message = serializers.CharField(max_length=1000, required=False, allow_blank=True, default="")


class VenueShareRequestAcceptSerializer(serializers.Serializer):
    layout = serializers.ChoiceField(
        choices=[(LAYOUT_OURS, "Use our tables"), (LAYOUT_THEIRS, "Use their tables")],
        required=False,
        allow_null=True,
        help_text="Only when this acceptance creates the venue",
    )
    venue_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")


class VenueLeaveSerializer(serializers.Serializer):
    confirm = serializers.BooleanField()

    def validate_confirm(self, value):
        if not value:
            raise serializers.ValidationError("Set confirm=true to leave the venue.")
        return value


class VenueMemberSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    logo = serializers.ImageField(allow_null=True)
    display_order = serializers.IntegerField()
    is_layout_seed = serializers.BooleanField()
    is_me = serializers.BooleanField()


class VenueSectionDashboardSerializer(serializers.ModelSerializer):
    class Meta:
        model = VenueSection
        fields = ["id", "name", "description", "display_order", "is_active"]


class VenueSectionWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    display_order = serializers.IntegerField(required=False, min_value=0)
    is_active = serializers.BooleanField(required=False)


class VenueTableDashboardSerializer(serializers.ModelSerializer):
    section_name = serializers.CharField(source="section.name", read_only=True, default=None)
    qr_url = serializers.SerializerMethodField()
    local_table_id = serializers.SerializerMethodField()

    class Meta:
        model = VenueTable
        fields = [
            "id",
            "number",
            "name",
            "capacity",
            "min_capacity",
            "shape",
            "section",
            "section_name",
            "is_active",
            "code",
            "qr_url",
            "qr_image",
            "local_table_id",
        ]

    def get_qr_url(self, obj):
        return obj.get_qr_url()

    def get_local_table_id(self, obj):
        local = (self.context.get("local_tables") or {}).get(obj.pk)
        return str(local.pk) if local else None


class VenueTableWriteSerializer(serializers.Serializer):
    number = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    capacity = serializers.IntegerField(required=False, min_value=1)
    min_capacity = serializers.IntegerField(required=False, min_value=1)
    shape = serializers.ChoiceField(choices=["square", "round", "rectangle"], required=False)
    section = serializers.UUIDField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)


class VenuePermissionsSerializer(serializers.Serializer):
    can_manage = serializers.BooleanField()
    can_leave = serializers.BooleanField()


class VenueStateSerializer(serializers.Serializer):
    venue = VenueCardSerializer(allow_null=True)
    members = VenueMemberSerializer(many=True)
    shared_tables_count = serializers.IntegerField()
    incoming_requests = VenueShareRequestSerializer(many=True)
    outgoing_requests = VenueShareRequestSerializer(many=True)
    permissions = VenuePermissionsSerializer()


class VenueStateResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = VenueStateSerializer()


class SyncSummarySerializer(serializers.Serializer):
    created = serializers.IntegerField()
    linked = serializers.IntegerField()
    updated = serializers.IntegerField()
    conflicts = serializers.ListField(child=serializers.DictField())
