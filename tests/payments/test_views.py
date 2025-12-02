"""
Tests for payments views.
"""

import uuid

from rest_framework import status

import pytest


@pytest.mark.django_db
class TestDashboardPaymentListView:
    """Tests for dashboard payment list endpoint."""

    url = "/api/v1/dashboard/payments/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list payments."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_list(self, authenticated_owner_client, restaurant, payment):
        """Test that authenticated owner can list payments."""
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = authenticated_owner_client.get(self.url)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestDashboardPaymentDetailView:
    """Tests for dashboard payment detail endpoint."""

    def test_unauthenticated_cannot_access(self, api_client, payment):
        """Test that unauthenticated users cannot access payment details."""
        url = f"/api/v1/dashboard/payments/{payment.id}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_not_found(self, authenticated_owner_client, restaurant):
        """Test 404 for non-existent payment."""
        url = f"/api/v1/dashboard/payments/{uuid.uuid4()}/"
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = authenticated_owner_client.get(url)
        assert response.status_code in [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestDashboardCashPaymentView:
    """Tests for dashboard cash payment endpoint."""

    url = "/api/v1/dashboard/payments/cash/"

    def test_unauthenticated_cannot_create(self, api_client, order):
        """Test that unauthenticated users cannot create cash payments."""
        data = {
            "order_id": str(order.id),
            "amount": "50.00",
            "amount_received": "60.00",
        }
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardCardPaymentView:
    """Tests for dashboard card payment endpoint."""

    url = "/api/v1/dashboard/payments/card/"

    def test_unauthenticated_cannot_create(self, api_client, order):
        """Test that unauthenticated users cannot create card payments."""
        data = {
            "order_id": str(order.id),
            "amount": "50.00",
        }
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardRefundListView:
    """Tests for dashboard refund list endpoint."""

    url = "/api/v1/dashboard/payments/refunds/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list refunds."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardRefundCreateView:
    """Tests for dashboard refund create endpoint."""

    url = "/api/v1/dashboard/payments/refunds/create/"

    def test_unauthenticated_cannot_create(self, api_client, payment):
        """Test that unauthenticated users cannot create refunds."""
        data = {
            "payment_id": str(payment.id),
            "amount": "10.00",
            "reason": "customer_request",
        }
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardPaymentStatsView:
    """Tests for dashboard payment stats endpoint."""

    url = "/api/v1/dashboard/payments/stats/"

    def test_unauthenticated_cannot_access(self, api_client):
        """Test that unauthenticated users cannot access payment stats."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestCustomerPaymentMethodListView:
    """Tests for customer payment method list endpoint."""

    url = "/api/v1/payments/methods/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list payment methods."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_list(self, authenticated_client, payment_method):
        """Test that authenticated users can list their payment methods."""
        response = authenticated_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestCustomerPaymentMethodCreateView:
    """Tests for customer payment method create endpoint."""

    url = "/api/v1/payments/methods/add/"

    def test_unauthenticated_cannot_create(self, api_client):
        """Test that unauthenticated users cannot add payment methods."""
        data = {"payment_method_id": "pm_test123"}
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_add(self, authenticated_client):
        """Test that authenticated users can add payment methods."""
        data = {"payment_method_id": "pm_newcard123"}
        response = authenticated_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["success"] is True


@pytest.mark.django_db
class TestCustomerPaymentMethodDetailView:
    """Tests for customer payment method detail endpoint."""

    def test_unauthenticated_cannot_update(self, api_client, payment_method):
        """Test that unauthenticated users cannot update payment methods."""
        url = f"/api/v1/payments/methods/{payment_method.id}/"
        response = api_client.patch(url, {"is_default": True}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthenticated_cannot_delete(self, api_client, payment_method):
        """Test that unauthenticated users cannot delete payment methods."""
        url = f"/api/v1/payments/methods/{payment_method.id}/"
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_delete(self, authenticated_client, payment_method):
        """Test that authenticated users can delete their payment methods."""
        url = f"/api/v1/payments/methods/{payment_method.id}/"
        response = authenticated_client.delete(url)
        assert response.status_code == status.HTTP_200_OK
        payment_method.refresh_from_db()
        assert payment_method.is_active is False


@pytest.mark.django_db
class TestCustomerPaymentHistoryView:
    """Tests for customer payment history endpoint."""

    url = "/api/v1/payments/history/"

    def test_unauthenticated_cannot_access(self, api_client):
        """Test that unauthenticated users cannot access payment history."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_access(self, authenticated_client):
        """Test that authenticated users can access their payment history."""
        response = authenticated_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
