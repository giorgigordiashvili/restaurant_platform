"""
Tests for reservation models.
"""

from datetime import date, time, timedelta

from django.utils import timezone

import pytest

from apps.reservations.models import (
    Reservation,
    ReservationBlockedTime,
    ReservationHistory,
    ReservationSettings,
)


@pytest.mark.django_db
class TestReservationSettingsModel:
    """Tests for ReservationSettings model."""

    def test_create_settings(self, restaurant):
        """Test creating reservation settings."""
        settings = ReservationSettings.objects.create(
            restaurant=restaurant,
            min_party_size=2,
            max_party_size=10,
        )
        assert settings.restaurant == restaurant
        assert settings.min_party_size == 2
        assert settings.max_party_size == 10
        assert settings.accepts_reservations is True

    def test_settings_str(self, restaurant):
        """Test settings string representation."""
        settings = ReservationSettings.objects.create(restaurant=restaurant)
        assert restaurant.name in str(settings)

    def test_default_values(self, restaurant):
        """Test default values for settings."""
        settings = ReservationSettings.objects.create(restaurant=restaurant)
        assert settings.accepts_reservations is True
        assert settings.min_party_size == 1
        assert settings.max_party_size == 20
        assert settings.advance_booking_days == 30
        assert settings.min_advance_hours == 2
        assert settings.buffer_minutes == 15
        assert settings.slot_interval_minutes == 30
        assert settings.cancellation_deadline_hours == 24


@pytest.mark.django_db
class TestReservationModel:
    """Tests for Reservation model."""

    def test_create_reservation(self, restaurant):
        """Test creating a reservation."""
        tomorrow = timezone.now().date() + timedelta(days=1)
        reservation = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="John Doe",
            guest_phone="+1234567890",
            reservation_date=tomorrow,
            reservation_time=time(19, 0),
            party_size=4,
        )
        assert reservation.guest_name == "John Doe"
        assert reservation.party_size == 4
        assert reservation.status == "pending"
        assert reservation.confirmation_code is not None
        assert len(reservation.confirmation_code) == 8

    def test_reservation_str(self, restaurant):
        """Test reservation string representation."""
        reservation = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="John Doe",
            guest_phone="+1234567890",
            reservation_date=date.today() + timedelta(days=1),
            reservation_time=time(19, 0),
            party_size=4,
        )
        assert "John Doe" in str(reservation)
        assert "4" in str(reservation)
        assert reservation.confirmation_code in str(reservation)

    def test_confirmation_code_unique(self, restaurant):
        """Test that confirmation codes are unique."""
        res1 = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="Guest 1",
            guest_phone="+1234567890",
            reservation_date=date.today() + timedelta(days=1),
            reservation_time=time(19, 0),
            party_size=2,
        )
        res2 = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="Guest 2",
            guest_phone="+1234567891",
            reservation_date=date.today() + timedelta(days=1),
            reservation_time=time(20, 0),
            party_size=2,
        )
        assert res1.confirmation_code != res2.confirmation_code

    def test_is_upcoming(self, restaurant):
        """Test is_upcoming property."""
        tomorrow = timezone.now().date() + timedelta(days=1)
        yesterday = timezone.now().date() - timedelta(days=1)

        future_res = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="Future Guest",
            guest_phone="+1234567890",
            reservation_date=tomorrow,
            reservation_time=time(19, 0),
            party_size=2,
        )
        past_res = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="Past Guest",
            guest_phone="+1234567890",
            reservation_date=yesterday,
            reservation_time=time(19, 0),
            party_size=2,
        )
        assert future_res.is_upcoming is True
        assert past_res.is_upcoming is False

    def test_can_modify(self, restaurant):
        """Test can_modify property."""
        tomorrow = timezone.now().date() + timedelta(days=1)
        reservation = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="Test Guest",
            guest_phone="+1234567890",
            reservation_date=tomorrow,
            reservation_time=time(19, 0),
            party_size=2,
            status="confirmed",
        )
        assert reservation.can_modify is True

        # Cancelled reservation cannot be modified
        reservation.status = "cancelled"
        reservation.save()
        assert reservation.can_modify is False

    def test_confirm_reservation(self, restaurant, user):
        """Test confirming a reservation."""
        reservation = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="Test Guest",
            guest_phone="+1234567890",
            reservation_date=date.today() + timedelta(days=1),
            reservation_time=time(19, 0),
            party_size=2,
        )
        assert reservation.status == "pending"

        reservation.confirm(confirmed_by=user)

        reservation.refresh_from_db()
        assert reservation.status == "confirmed"
        assert reservation.confirmed_by == user
        assert reservation.confirmed_at is not None

    def test_cancel_reservation(self, restaurant, user):
        """Test cancelling a reservation."""
        reservation = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="Test Guest",
            guest_phone="+1234567890",
            reservation_date=date.today() + timedelta(days=1),
            reservation_time=time(19, 0),
            party_size=2,
            status="confirmed",
        )
        reservation.cancel(cancelled_by=user, reason="Changed plans")

        reservation.refresh_from_db()
        assert reservation.status == "cancelled"
        assert reservation.cancelled_by == user
        assert reservation.cancelled_at is not None
        assert reservation.cancellation_reason == "Changed plans"

    def test_mark_seated(self, restaurant):
        """Test marking reservation as seated."""
        reservation = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="Test Guest",
            guest_phone="+1234567890",
            reservation_date=date.today(),
            reservation_time=time(19, 0),
            party_size=2,
            status="confirmed",
        )
        reservation.mark_seated()

        reservation.refresh_from_db()
        assert reservation.status == "seated"
        assert reservation.seated_at is not None

    def test_mark_completed(self, restaurant):
        """Test marking reservation as completed."""
        reservation = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="Test Guest",
            guest_phone="+1234567890",
            reservation_date=date.today(),
            reservation_time=time(19, 0),
            party_size=2,
            status="seated",
        )
        reservation.mark_completed()

        reservation.refresh_from_db()
        assert reservation.status == "completed"
        assert reservation.completed_at is not None

    def test_mark_no_show(self, restaurant):
        """Test marking reservation as no-show."""
        reservation = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="Test Guest",
            guest_phone="+1234567890",
            reservation_date=date.today(),
            reservation_time=time(19, 0),
            party_size=2,
            status="confirmed",
        )
        reservation.mark_no_show()

        reservation.refresh_from_db()
        assert reservation.status == "no_show"


@pytest.mark.django_db
class TestReservationBlockedTimeModel:
    """Tests for ReservationBlockedTime model."""

    def test_create_blocked_time(self, restaurant):
        """Test creating a blocked time."""
        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(hours=4)
        blocked = ReservationBlockedTime.objects.create(
            restaurant=restaurant,
            start_datetime=start,
            end_datetime=end,
            reason="private_event",
            description="VIP dinner",
        )
        assert blocked.restaurant == restaurant
        assert blocked.reason == "private_event"

    def test_blocked_time_str(self, restaurant):
        """Test blocked time string representation."""
        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(hours=4)
        blocked = ReservationBlockedTime.objects.create(
            restaurant=restaurant,
            start_datetime=start,
            end_datetime=end,
            reason="holiday",
        )
        assert "Holiday" in str(blocked)

    def test_is_active(self, restaurant):
        """Test is_active property."""
        # Active block
        active_block = ReservationBlockedTime.objects.create(
            restaurant=restaurant,
            start_datetime=timezone.now() - timedelta(hours=1),
            end_datetime=timezone.now() + timedelta(hours=1),
            reason="maintenance",
        )
        assert active_block.is_active is True

        # Future block
        future_block = ReservationBlockedTime.objects.create(
            restaurant=restaurant,
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=4),
            reason="maintenance",
        )
        assert future_block.is_active is False

    def test_is_all_tables(self, restaurant, table):
        """Test is_all_tables property."""
        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(hours=4)

        # Block all tables
        all_block = ReservationBlockedTime.objects.create(
            restaurant=restaurant,
            start_datetime=start,
            end_datetime=end,
            reason="holiday",
        )
        assert all_block.is_all_tables is True

        # Block specific tables
        specific_block = ReservationBlockedTime.objects.create(
            restaurant=restaurant,
            start_datetime=start + timedelta(days=1),
            end_datetime=end + timedelta(days=1),
            reason="private_event",
        )
        specific_block.tables.add(table)
        assert specific_block.is_all_tables is False


@pytest.mark.django_db
class TestReservationHistoryModel:
    """Tests for ReservationHistory model."""

    def test_create_history(self, restaurant, user):
        """Test creating reservation history."""
        reservation = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="Test Guest",
            guest_phone="+1234567890",
            reservation_date=date.today() + timedelta(days=1),
            reservation_time=time(19, 0),
            party_size=2,
        )
        history = ReservationHistory.objects.create(
            reservation=reservation,
            previous_status="pending",
            new_status="confirmed",
            changed_by=user,
            notes="Confirmed by staff",
        )
        assert history.reservation == reservation
        assert history.previous_status == "pending"
        assert history.new_status == "confirmed"
        assert history.changed_by == user

    def test_history_str(self, restaurant, user):
        """Test history string representation."""
        reservation = Reservation.objects.create(
            restaurant=restaurant,
            guest_name="Test Guest",
            guest_phone="+1234567890",
            reservation_date=date.today() + timedelta(days=1),
            reservation_time=time(19, 0),
            party_size=2,
        )
        history = ReservationHistory.objects.create(
            reservation=reservation,
            previous_status="pending",
            new_status="confirmed",
            changed_by=user,
        )
        assert reservation.confirmation_code in str(history)
        assert "pending" in str(history)
        assert "confirmed" in str(history)
