"""
Tests for reservation views.
"""

import uuid
from datetime import date, time, timedelta

from django.utils import timezone

from rest_framework import status

import pytest


@pytest.mark.django_db
class TestPublicReservationSettingsView:
    """Tests for public reservation settings endpoint."""

    url = "/api/v1/reservations/settings/"

    def test_get_settings(self, api_client, restaurant, reservation_settings):
        """Test getting public reservation settings."""
        api_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert "accepts_reservations" in response.data
        assert "min_party_size" in response.data
        assert "max_party_size" in response.data


@pytest.mark.django_db
class TestPublicAvailabilityView:
    """Tests for public availability endpoint."""

    url = "/api/v1/reservations/availability/"

    def test_check_availability(self, api_client, restaurant, reservation_settings):
        """Test checking availability for a date."""
        api_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        tomorrow = (timezone.now() + timedelta(days=1)).date()
        response = api_client.get(self.url, {"date": tomorrow.isoformat(), "party_size": 4})
        assert response.status_code == status.HTTP_200_OK
        assert "slots" in response.data

    def test_past_date_rejected(self, api_client, restaurant, reservation_settings):
        """Test that past dates are rejected."""
        api_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        yesterday = (timezone.now() - timedelta(days=1)).date()
        response = api_client.get(self.url, {"date": yesterday.isoformat(), "party_size": 4})
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestPublicReservationCreateView:
    """Tests for public reservation create endpoint."""

    url = "/api/v1/reservations/create/"

    def test_create_reservation(self, api_client, restaurant, reservation_settings):
        """Test creating a reservation."""
        api_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        tomorrow = (timezone.now() + timedelta(days=1)).date()
        data = {
            "guest_name": "John Doe",
            "guest_email": "john@example.com",
            "guest_phone": "+1234567890",
            "reservation_date": tomorrow.isoformat(),
            "reservation_time": "19:00:00",
            "party_size": 4,
            "special_requests": "Window seat preferred",
        }
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["success"] is True
        assert "confirmation_code" in response.data

    def test_create_reservation_missing_fields(self, api_client, restaurant, reservation_settings):
        """Test creating reservation with missing fields fails."""
        api_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        data = {"guest_name": "John Doe"}
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestPublicReservationLookupView:
    """Tests for public reservation lookup endpoint."""

    url = "/api/v1/reservations/lookup/"

    def test_lookup_reservation(self, api_client, restaurant, reservation):
        """Test looking up a reservation by code."""
        api_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = api_client.get(self.url, {"code": reservation.confirmation_code})
        assert response.status_code == status.HTTP_200_OK
        assert response.data["confirmation_code"] == reservation.confirmation_code

    def test_lookup_not_found(self, api_client, restaurant):
        """Test looking up non-existent reservation."""
        api_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = api_client.get(self.url, {"code": "NOTFOUND"})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_lookup_missing_code(self, api_client, restaurant):
        """Test lookup without confirmation code."""
        api_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestPublicReservationCancelView:
    """Tests for public reservation cancel endpoint."""

    url = "/api/v1/reservations/cancel/"

    def test_cancel_reservation(self, api_client, restaurant, reservation):
        """Test cancelling a reservation."""
        api_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        data = {
            "confirmation_code": reservation.confirmation_code,
            "reason": "Changed plans",
        }
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True

    def test_cancel_not_found(self, api_client, restaurant):
        """Test cancelling non-existent reservation."""
        api_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        data = {"confirmation_code": "NOTFOUND"}
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestCustomerReservationListView:
    """Tests for customer reservation list endpoint."""

    url = "/api/v1/reservations/my/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list reservations."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_list(self, authenticated_client, user, restaurant, create_reservation):
        """Test that authenticated users can list their reservations."""
        reservation = create_reservation(restaurant=restaurant, customer=user)
        response = authenticated_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestDashboardReservationListView:
    """Tests for dashboard reservation list endpoint."""

    url = "/api/v1/dashboard/reservations/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list reservations."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_owner_can_list(self, authenticated_owner_client, restaurant, reservation):
        """Test that authenticated owner can list reservations."""
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = authenticated_owner_client.get(self.url)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestDashboardReservationCreateView:
    """Tests for dashboard reservation create endpoint."""

    url = "/api/v1/dashboard/reservations/create/"

    def test_unauthenticated_cannot_create(self, api_client, restaurant):
        """Test that unauthenticated users cannot create reservations."""
        tomorrow = (timezone.now() + timedelta(days=1)).date()
        data = {
            "guest_name": "John Doe",
            "guest_phone": "+1234567890",
            "reservation_date": tomorrow.isoformat(),
            "reservation_time": "19:00:00",
            "party_size": 4,
        }
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardReservationDetailView:
    """Tests for dashboard reservation detail endpoint."""

    def test_unauthenticated_cannot_access(self, api_client, reservation):
        """Test that unauthenticated users cannot access reservation details."""
        url = f"/api/v1/dashboard/reservations/{reservation.id}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_not_found(self, authenticated_owner_client, restaurant):
        """Test 404 for non-existent reservation."""
        url = f"/api/v1/dashboard/reservations/{uuid.uuid4()}/"
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = authenticated_owner_client.get(url)
        assert response.status_code in [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestDashboardReservationStatusView:
    """Tests for dashboard reservation status update endpoint."""

    def test_unauthenticated_cannot_update_status(self, api_client, reservation):
        """Test that unauthenticated users cannot update status."""
        url = f"/api/v1/dashboard/reservations/{reservation.id}/status/"
        data = {"status": "confirmed"}
        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardTodayReservationsView:
    """Tests for dashboard today's reservations endpoint."""

    url = "/api/v1/dashboard/reservations/today/"

    def test_unauthenticated_cannot_access(self, api_client):
        """Test that unauthenticated users cannot access today's reservations."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardUpcomingReservationsView:
    """Tests for dashboard upcoming reservations endpoint."""

    url = "/api/v1/dashboard/reservations/upcoming/"

    def test_unauthenticated_cannot_access(self, api_client):
        """Test that unauthenticated users cannot access upcoming reservations."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardReservationStatsView:
    """Tests for dashboard reservation stats endpoint."""

    url = "/api/v1/dashboard/reservations/stats/"

    def test_unauthenticated_cannot_access(self, api_client):
        """Test that unauthenticated users cannot access stats."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardBlockedTimeListView:
    """Tests for dashboard blocked time list endpoint."""

    url = "/api/v1/dashboard/reservations/blocked-times/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list blocked times."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthenticated_cannot_create(self, api_client, restaurant):
        """Test that unauthenticated users cannot create blocked times."""
        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(hours=4)
        data = {
            "start_datetime": start.isoformat(),
            "end_datetime": end.isoformat(),
            "reason": "holiday",
        }
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardReservationSettingsView:
    """Tests for dashboard reservation settings endpoint."""

    url = "/api/v1/dashboard/reservations/settings/"

    def test_unauthenticated_cannot_access(self, api_client):
        """Test that unauthenticated users cannot access settings."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
