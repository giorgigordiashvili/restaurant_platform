"""
Serializers for BOG initiate / status / webhook endpoints.

Keep these separate from the existing Stripe-oriented serializers in
``apps.payments.serializers`` so the two schemas don't drift.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.menu.models import MenuItem, Modifier


class BogOrderItemSerializer(serializers.Serializer):
    """
    Validates a cart item inside an initiate-payment body.

    Mirrors ``apps.orders.serializers.BogOrderItemSerializer`` but doesn't
    require a ``restaurant`` context — the enclosing view verifies that each
    resolved ``MenuItem`` belongs to the target restaurant before creating
    anything. Keeping this lookup context-free lets us reuse the same
    serializer for both the order and reservation initiate flows (the
    restaurant is only known after we've parsed ``restaurant_slug``).
    """

    menu_item_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, default=1)
    modifier_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )
    special_instructions = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_menu_item_id(self, value):
        try:
            return MenuItem.objects.get(id=value, is_available=True)
        except MenuItem.DoesNotExist as exc:
            raise serializers.ValidationError("Menu item not found or unavailable.") from exc

    def validate_modifier_ids(self, value):
        if not value:
            return []
        modifiers = list(Modifier.objects.filter(id__in=value, is_available=True))
        if len(modifiers) != len(value):
            raise serializers.ValidationError("One or more modifiers not found or unavailable.")
        return modifiers

ALLOWED_RETURN_SCHEMES = {"http", "https"}


class ReturnURLMixin(serializers.Serializer):
    """
    Shared validator for the ``return_url`` customer-landing URL.

    Blocking non-http(s) schemes keeps us from shipping ``javascript:`` or
    ``file://`` into BOG's ``redirect_urls``. We still rely on the backend being
    behind a trusted edge — downstream, we should also restrict to known hosts,
    but that's an ops concern.
    """

    return_url = serializers.URLField(max_length=1024)

    def validate_return_url(self, value: str) -> str:
        # URLField already enforces a scheme; belt-and-braces re-check.
        from urllib.parse import urlparse

        parsed = urlparse(value)
        if parsed.scheme not in ALLOWED_RETURN_SCHEMES:
            raise serializers.ValidationError("return_url must use http or https.")
        if not parsed.netloc:
            raise serializers.ValidationError("return_url must include a host.")
        return value


class OrderPayloadSerializer(serializers.Serializer):
    """
    Shape the frontend POSTs as ``order_payload`` inside the initiate request.

    Mirrors ``apps.orders.views.CustomerOrderCreateView`` expectations so we can
    reuse the same item validation.
    """

    restaurant_slug = serializers.SlugField(max_length=100)
    order_type = serializers.ChoiceField(
        choices=[("dine_in", "Dine In"), ("takeaway", "Takeaway"), ("delivery", "Delivery")],
        default="dine_in",
    )
    table_id = serializers.UUIDField(required=False, allow_null=True)
    table_session = serializers.UUIDField(required=False, allow_null=True)
    customer_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    customer_phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    customer_email = serializers.EmailField(required=False, allow_blank=True, default="")
    customer_notes = serializers.CharField(required=False, allow_blank=True, default="")
    delivery_address = serializers.CharField(required=False, allow_blank=True, default="")
    tip_amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=0, required=False, default=0
    )
    items = BogOrderItemSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("Order must contain at least one item.")
        return value


class ReservationPayloadSerializer(serializers.Serializer):
    """Reservation fields for the initiate-with-payment flow."""

    restaurant_slug = serializers.SlugField(max_length=100)
    guest_name = serializers.CharField(max_length=255)
    guest_phone = serializers.CharField(max_length=20)
    guest_email = serializers.EmailField(required=False, allow_blank=True, default="")
    reservation_date = serializers.DateField()
    reservation_time = serializers.TimeField()
    party_size = serializers.IntegerField(min_value=1)
    special_requests = serializers.CharField(required=False, allow_blank=True, default="")
    # Optional — overrides the restaurant's default deposit_amount / env fallback.
    deposit_amount_override = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        min_value=0,
    )
    # Optional pre-order bundle: menu items the guest wants prepared for arrival.
    # When present we create an Order(status=pending_payment, reservation=...) and
    # charge deposit + order total in a single BOG transaction.
    items = BogOrderItemSerializer(many=True, required=False, default=list)


class InitiatePaymentSerializer(ReturnURLMixin, serializers.Serializer):
    """Top-level initiate body. ``target`` discriminates the payload."""

    TARGET_ORDER = "order"
    TARGET_RESERVATION = "reservation"
    TARGET_CHOICES = [(TARGET_ORDER, "Order"), (TARGET_RESERVATION, "Reservation")]

    target = serializers.ChoiceField(choices=TARGET_CHOICES)
    order_payload = OrderPayloadSerializer(required=False)
    reservation_payload = ReservationPayloadSerializer(required=False)

    def validate(self, attrs):
        target = attrs["target"]
        if target == self.TARGET_ORDER and "order_payload" not in attrs:
            raise serializers.ValidationError(
                {"order_payload": "order_payload is required when target='order'."}
            )
        if target == self.TARGET_RESERVATION and "reservation_payload" not in attrs:
            raise serializers.ValidationError(
                {"reservation_payload": "reservation_payload is required when target='reservation'."}
            )
        return attrs


class InitiateAddCardSerializer(ReturnURLMixin, serializers.Serializer):
    """Body for the tokenisation (add-card) initiate endpoint."""


class BogStatusResponseSerializer(serializers.Serializer):
    """Shape we return from /bog/status/<id>/."""

    status = serializers.CharField()
    code = serializers.IntegerField(allow_null=True, required=False)
    code_description = serializers.CharField(allow_blank=True, required=False)
    order_number = serializers.CharField(allow_null=True, required=False)
    reservation_id = serializers.CharField(allow_null=True, required=False)
    payment_method_id = serializers.CharField(allow_null=True, required=False)
