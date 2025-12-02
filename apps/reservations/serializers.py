"""
Serializers for reservations.
"""

from datetime import datetime, timedelta

from django.utils import timezone
from rest_framework import serializers

from apps.tables.models import Table

from .models import (
    Reservation,
    ReservationBlockedTime,
    ReservationHistory,
    ReservationSettings,
)


class ReservationSettingsSerializer(serializers.ModelSerializer):
    """Serializer for reservation settings (public view)."""

    class Meta:
        model = ReservationSettings
        fields = [
            "accepts_reservations",
            "min_party_size",
            "max_party_size",
            "advance_booking_days",
            "min_advance_hours",
            "slot_interval_minutes",
            "cancellation_deadline_hours",
        ]
        read_only_fields = fields


class ReservationSettingsAdminSerializer(serializers.ModelSerializer):
    """Serializer for reservation settings (admin/dashboard view)."""

    class Meta:
        model = ReservationSettings
        fields = [
            "id",
            "accepts_reservations",
            "min_party_size",
            "max_party_size",
            "reservation_duration",
            "advance_booking_days",
            "min_advance_hours",
            "buffer_minutes",
            "slot_interval_minutes",
            "cancellation_deadline_hours",
            "require_confirmation",
            "auto_confirm_threshold",
            "send_reminder",
            "reminder_hours_before",
            "max_daily_reservations",
            "max_hourly_reservations",
        ]


class ReservationHistorySerializer(serializers.ModelSerializer):
    """Serializer for reservation history."""

    changed_by_email = serializers.EmailField(source="changed_by.email", read_only=True)

    class Meta:
        model = ReservationHistory
        fields = [
            "id",
            "previous_status",
            "new_status",
            "changed_by",
            "changed_by_email",
            "notes",
            "created_at",
        ]
        read_only_fields = fields


class ReservationListSerializer(serializers.ModelSerializer):
    """Serializer for reservation list view."""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    source_display = serializers.CharField(source="get_source_display", read_only=True)
    table_number = serializers.CharField(source="table.number", read_only=True)
    can_cancel = serializers.BooleanField(read_only=True)
    can_modify = serializers.BooleanField(read_only=True)

    class Meta:
        model = Reservation
        fields = [
            "id",
            "confirmation_code",
            "guest_name",
            "guest_phone",
            "reservation_date",
            "reservation_time",
            "party_size",
            "status",
            "status_display",
            "source",
            "source_display",
            "table",
            "table_number",
            "can_cancel",
            "can_modify",
            "created_at",
        ]
        read_only_fields = fields


class ReservationDetailSerializer(serializers.ModelSerializer):
    """Serializer for reservation detail view."""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    source_display = serializers.CharField(source="get_source_display", read_only=True)
    table_number = serializers.CharField(source="table.number", read_only=True)
    customer_email = serializers.EmailField(source="customer.email", read_only=True)
    can_cancel = serializers.BooleanField(read_only=True)
    can_modify = serializers.BooleanField(read_only=True)
    is_upcoming = serializers.BooleanField(read_only=True)
    history = ReservationHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Reservation
        fields = [
            "id",
            "confirmation_code",
            "restaurant",
            "customer",
            "customer_email",
            "guest_name",
            "guest_email",
            "guest_phone",
            "reservation_date",
            "reservation_time",
            "party_size",
            "duration",
            "table",
            "table_number",
            "status",
            "status_display",
            "source",
            "source_display",
            "special_requests",
            "internal_notes",
            "confirmed_at",
            "cancelled_at",
            "cancellation_reason",
            "seated_at",
            "completed_at",
            "can_cancel",
            "can_modify",
            "is_upcoming",
            "history",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "confirmation_code",
            "confirmed_at",
            "cancelled_at",
            "seated_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]


class ReservationCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a reservation (public)."""

    class Meta:
        model = Reservation
        fields = [
            "guest_name",
            "guest_email",
            "guest_phone",
            "reservation_date",
            "reservation_time",
            "party_size",
            "special_requests",
        ]

    def validate_reservation_date(self, value):
        """Validate reservation date is not in the past."""
        if value < timezone.now().date():
            raise serializers.ValidationError("Reservation date cannot be in the past.")
        return value

    def validate_party_size(self, value):
        """Validate party size against restaurant settings."""
        restaurant = self.context.get("restaurant")
        if restaurant:
            try:
                settings = restaurant.reservation_settings
                if value < settings.min_party_size:
                    raise serializers.ValidationError(
                        f"Minimum party size is {settings.min_party_size}."
                    )
                if value > settings.max_party_size:
                    raise serializers.ValidationError(
                        f"Maximum party size is {settings.max_party_size}."
                    )
            except ReservationSettings.DoesNotExist:
                pass
        return value

    def validate(self, attrs):
        """Validate the reservation request."""
        restaurant = self.context.get("restaurant")
        if not restaurant:
            raise serializers.ValidationError("Restaurant context is required.")

        # Check if restaurant accepts reservations
        try:
            settings = restaurant.reservation_settings
            if not settings.accepts_reservations:
                raise serializers.ValidationError(
                    "This restaurant does not accept online reservations."
                )

            # Check advance booking window
            reservation_datetime = timezone.make_aware(
                datetime.combine(attrs["reservation_date"], attrs["reservation_time"])
            )
            now = timezone.now()

            # Min advance hours
            min_booking_time = now + timedelta(hours=settings.min_advance_hours)
            if reservation_datetime < min_booking_time:
                raise serializers.ValidationError(
                    f"Reservations must be made at least {settings.min_advance_hours} hours in advance."
                )

            # Max advance days
            max_booking_date = now.date() + timedelta(days=settings.advance_booking_days)
            if attrs["reservation_date"] > max_booking_date:
                raise serializers.ValidationError(
                    f"Reservations can only be made up to {settings.advance_booking_days} days in advance."
                )

        except ReservationSettings.DoesNotExist:
            pass

        return attrs

    def create(self, validated_data):
        """Create reservation with restaurant context."""
        restaurant = self.context.get("restaurant")
        user = self.context.get("request").user

        validated_data["restaurant"] = restaurant

        # Assign logged-in user if available
        if user and user.is_authenticated:
            validated_data["customer"] = user

        # Auto-confirm based on settings
        try:
            settings = restaurant.reservation_settings
            if not settings.require_confirmation:
                validated_data["status"] = "confirmed"
            elif validated_data.get("party_size", 0) <= settings.auto_confirm_threshold:
                validated_data["status"] = "confirmed"
        except ReservationSettings.DoesNotExist:
            validated_data["status"] = "confirmed"

        return super().create(validated_data)


class ReservationDashboardCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a reservation (dashboard/staff)."""

    class Meta:
        model = Reservation
        fields = [
            "customer",
            "guest_name",
            "guest_email",
            "guest_phone",
            "reservation_date",
            "reservation_time",
            "party_size",
            "duration",
            "table",
            "status",
            "source",
            "special_requests",
            "internal_notes",
        ]

    def validate_table(self, value):
        """Validate table belongs to the restaurant."""
        restaurant = self.context.get("restaurant")
        if value and restaurant and value.restaurant != restaurant:
            raise serializers.ValidationError("Table does not belong to this restaurant.")
        return value

    def create(self, validated_data):
        """Create reservation with restaurant context."""
        restaurant = self.context.get("restaurant")
        validated_data["restaurant"] = restaurant
        return super().create(validated_data)


class ReservationUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating a reservation."""

    class Meta:
        model = Reservation
        fields = [
            "guest_name",
            "guest_email",
            "guest_phone",
            "reservation_date",
            "reservation_time",
            "party_size",
            "table",
            "special_requests",
            "internal_notes",
        ]

    def validate(self, attrs):
        """Validate update is allowed."""
        instance = self.instance
        if instance and not instance.can_modify:
            raise serializers.ValidationError(
                "This reservation can no longer be modified."
            )
        return attrs


class ReservationStatusUpdateSerializer(serializers.Serializer):
    """Serializer for updating reservation status."""

    status = serializers.ChoiceField(choices=Reservation.STATUS_CHOICES)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_status(self, value):
        """Validate status transition."""
        instance = self.instance
        if not instance:
            return value

        invalid_transitions = {
            "completed": ["pending", "confirmed", "waitlist", "seated"],
            "cancelled": ["pending", "confirmed", "waitlist"],
            "no_show": ["pending", "confirmed", "waitlist"],
        }

        current_status = instance.status
        allowed_from = invalid_transitions.get(value, [])

        if value == "seated" and current_status not in ["pending", "confirmed"]:
            raise serializers.ValidationError(
                "Can only seat pending or confirmed reservations."
            )

        if value == "completed" and current_status != "seated":
            raise serializers.ValidationError(
                "Can only complete seated reservations."
            )

        return value

    def update(self, instance, validated_data):
        """Update reservation status and create history."""
        old_status = instance.status
        new_status = validated_data["status"]
        notes = validated_data.get("notes", "")
        user = self.context.get("request").user

        # Create history record
        ReservationHistory.objects.create(
            reservation=instance,
            previous_status=old_status,
            new_status=new_status,
            changed_by=user if user.is_authenticated else None,
            notes=notes,
        )

        # Update status using appropriate method
        if new_status == "confirmed":
            instance.confirm(confirmed_by=user if user.is_authenticated else None)
        elif new_status == "cancelled":
            instance.cancel(
                cancelled_by=user if user.is_authenticated else None,
                reason=notes,
            )
        elif new_status == "seated":
            instance.mark_seated()
        elif new_status == "completed":
            instance.mark_completed()
        elif new_status == "no_show":
            instance.mark_no_show()
        else:
            instance.status = new_status
            instance.save(update_fields=["status", "updated_at"])

        return instance


class ReservationCancelSerializer(serializers.Serializer):
    """Serializer for cancelling a reservation (customer)."""

    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate(self, attrs):
        """Validate cancellation is allowed."""
        instance = self.instance
        if not instance.can_cancel:
            raise serializers.ValidationError(
                "This reservation cannot be cancelled. Please contact the restaurant."
            )
        return attrs


class ReservationBlockedTimeSerializer(serializers.ModelSerializer):
    """Serializer for blocked times."""

    reason_display = serializers.CharField(source="get_reason_display", read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    is_all_tables = serializers.BooleanField(read_only=True)

    class Meta:
        model = ReservationBlockedTime
        fields = [
            "id",
            "start_datetime",
            "end_datetime",
            "tables",
            "reason",
            "reason_display",
            "description",
            "is_recurring",
            "recurrence_pattern",
            "is_active",
            "is_all_tables",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        """Validate time period."""
        start = attrs.get("start_datetime")
        end = attrs.get("end_datetime")
        if start and end and start >= end:
            raise serializers.ValidationError(
                {"end_datetime": "End time must be after start time."}
            )
        return attrs

    def create(self, validated_data):
        """Create blocked time with restaurant context."""
        restaurant = self.context.get("restaurant")
        user = self.context.get("request").user
        tables = validated_data.pop("tables", [])

        validated_data["restaurant"] = restaurant
        if user.is_authenticated:
            validated_data["created_by"] = user

        instance = super().create(validated_data)
        if tables:
            instance.tables.set(tables)
        return instance


class AvailableSlotSerializer(serializers.Serializer):
    """Serializer for available time slots."""

    date = serializers.DateField()
    time = serializers.TimeField()
    available_tables = serializers.IntegerField()


class AvailabilityRequestSerializer(serializers.Serializer):
    """Serializer for availability check request."""

    date = serializers.DateField()
    party_size = serializers.IntegerField(min_value=1)

    def validate_date(self, value):
        """Validate date is not in the past."""
        if value < timezone.now().date():
            raise serializers.ValidationError("Date cannot be in the past.")
        return value


class TableAssignmentSerializer(serializers.Serializer):
    """Serializer for assigning a table to a reservation."""

    table_id = serializers.UUIDField()

    def validate_table_id(self, value):
        """Validate table exists and belongs to restaurant."""
        restaurant = self.context.get("restaurant")
        try:
            table = Table.objects.get(id=value, restaurant=restaurant)
            return table
        except Table.DoesNotExist:
            raise serializers.ValidationError("Table not found.")
