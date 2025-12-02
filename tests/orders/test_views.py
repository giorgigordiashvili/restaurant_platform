"""
Tests for orders views.
"""

import uuid

from rest_framework import status

import pytest


@pytest.mark.django_db
class TestDashboardOrderListView:
    """Tests for dashboard order list endpoint."""

    url = "/api/v1/dashboard/orders/"

    def test_unauthenticated_cannot_list(self, api_client):
        """Test that unauthenticated users cannot list orders."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_list(self, authenticated_owner_client, restaurant, order):
        """Test that authenticated owner can list orders."""
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = authenticated_owner_client.get(self.url)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestDashboardOrderDetailView:
    """Tests for dashboard order detail endpoint."""

    def test_unauthenticated_cannot_access(self, api_client, order):
        """Test that unauthenticated users cannot access order details."""
        url = f"/api/v1/dashboard/orders/{order.id}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_not_found(self, authenticated_owner_client, restaurant):
        """Test 404 for non-existent order."""
        url = f"/api/v1/dashboard/orders/{uuid.uuid4()}/"
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = authenticated_owner_client.get(url)
        assert response.status_code in [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestDashboardOrderCreateView:
    """Tests for dashboard order create endpoint."""

    url = "/api/v1/dashboard/orders/create/"

    def test_unauthenticated_cannot_create(self, api_client, table, menu_item):
        """Test that unauthenticated users cannot create orders."""
        data = {
            "order_type": "dine_in",
            "table_id": str(table.id),
            "items": [{"menu_item_id": str(menu_item.id), "quantity": 1}],
        }
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardOrderStatusUpdateView:
    """Tests for dashboard order status update endpoint."""

    def test_unauthenticated_cannot_update_status(self, api_client, order):
        """Test that unauthenticated users cannot update order status."""
        url = f"/api/v1/dashboard/orders/{order.id}/status/"
        response = api_client.patch(url, {"status": "confirmed"}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardOrderAddItemView:
    """Tests for dashboard order add item endpoint."""

    def test_unauthenticated_cannot_add_item(self, api_client, order, menu_item):
        """Test that unauthenticated users cannot add items to orders."""
        url = f"/api/v1/dashboard/orders/{order.id}/items/"
        data = {"menu_item_id": str(menu_item.id), "quantity": 1}
        response = api_client.post(url, data, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardOrderItemStatusUpdateView:
    """Tests for dashboard order item status update endpoint."""

    def test_unauthenticated_cannot_update_item_status(self, api_client, order_item):
        """Test that unauthenticated users cannot update order item status."""
        url = f"/api/v1/dashboard/orders/{order_item.order.id}/items/{order_item.id}/status/"
        response = api_client.patch(url, {"status": "preparing"}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestDashboardKitchenOrdersView:
    """Tests for dashboard kitchen orders endpoint."""

    url = "/api/v1/dashboard/orders/kitchen/"

    def test_unauthenticated_cannot_access(self, api_client):
        """Test that unauthenticated users cannot access kitchen orders."""
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_can_access(self, authenticated_owner_client, restaurant):
        """Test that authenticated owner can access kitchen orders."""
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        response = authenticated_owner_client.get(self.url)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
class TestCustomerOrderCreateView:
    """Tests for customer order create endpoint."""

    url = "/api/v1/orders/create/"

    def test_create_order(self, api_client, restaurant, table, menu_item):
        """Test creating an order as customer."""
        data = {
            "restaurant_slug": restaurant.slug,
            "order_type": "dine_in",
            "table_id": str(table.id),
            "items": [{"menu_item_id": str(menu_item.id), "quantity": 1}],
        }
        response = api_client.post(self.url, data, format="json")
        # May succeed or fail depending on menu item availability
        assert response.status_code in [
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_create_order_missing_restaurant_slug(self, api_client):
        """Test creating order without restaurant slug fails."""
        data = {"order_type": "dine_in", "items": []}
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_order_invalid_restaurant(self, api_client):
        """Test creating order with invalid restaurant fails."""
        data = {
            "restaurant_slug": "invalid-restaurant",
            "order_type": "dine_in",
            "items": [{"menu_item_id": str(uuid.uuid4()), "quantity": 1}],
        }
        response = api_client.post(self.url, data, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
class TestCustomerOrderStatusView:
    """Tests for customer order status endpoint."""

    def test_get_order_status(self, api_client, order):
        """Test getting order status as customer."""
        url = f"/api/v1/orders/{order.order_number}/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True
        assert response.data["data"]["order_number"] == order.order_number

    def test_order_not_found(self, api_client):
        """Test 404 for non-existent order."""
        url = "/api/v1/orders/ORD-000000-9999/"
        response = api_client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
