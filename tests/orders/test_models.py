"""
Tests for orders models.
"""

from decimal import Decimal

import pytest


@pytest.mark.django_db
class TestOrderModel:
    """Tests for Order model."""

    def test_create_order(self, create_order, restaurant, table):
        """Test creating an order."""
        order = create_order(restaurant=restaurant, table=table)
        assert order.restaurant == restaurant
        assert order.table == table
        assert order.status == "pending"
        assert order.order_type == "dine_in"

    def test_order_str(self, order):
        """Test order string representation."""
        assert "ORD-" in str(order)

    def test_generate_order_number(self, order):
        """Test order number is generated."""
        assert order.order_number.startswith("ORD-")

    def test_unique_order_number(self, create_order, restaurant):
        """Test order numbers are unique."""
        order1 = create_order(restaurant=restaurant)
        order2 = create_order(restaurant=restaurant)
        assert order1.order_number != order2.order_number

    def test_calculate_totals(self, order, create_order_item, menu_item):
        """Test calculating order totals."""
        # Add items
        create_order_item(order=order, menu_item=menu_item, unit_price=Decimal("10.00"), quantity=2)
        create_order_item(order=order, menu_item=menu_item, unit_price=Decimal("5.00"), quantity=1)

        order.calculate_totals()
        assert order.subtotal == Decimal("25.00")

    def test_confirm_order(self, order):
        """Test confirming an order."""
        order.confirm(estimated_minutes=30)
        assert order.status == "confirmed"
        assert order.confirmed_at is not None
        assert order.estimated_ready_at is not None

    def test_cancel_order(self, order):
        """Test cancelling an order."""
        order.cancel(reason="Customer changed mind")
        assert order.status == "cancelled"
        assert order.cancelled_at is not None
        assert order.cancellation_reason == "Customer changed mind"

    def test_complete_order(self, order):
        """Test completing an order."""
        order.complete()
        assert order.status == "completed"
        assert order.completed_at is not None

    def test_is_editable(self, order):
        """Test is_editable property."""
        # Pending orders are editable
        order.status = "pending"
        assert order.is_editable is True

        # Confirmed orders are editable
        order.status = "confirmed"
        assert order.is_editable is True

        # Preparing orders are not editable
        order.status = "preparing"
        assert order.is_editable is False

    def test_can_cancel(self, order):
        """Test can_cancel property."""
        # Pending orders can be cancelled
        order.status = "pending"
        assert order.can_cancel is True

        # Completed orders cannot be cancelled
        order.status = "completed"
        assert order.can_cancel is False


@pytest.mark.django_db
class TestOrderItemModel:
    """Tests for OrderItem model."""

    def test_create_order_item(self, create_order_item, order, menu_item):
        """Test creating an order item."""
        item = create_order_item(
            order=order,
            menu_item=menu_item,
            item_name="Test Burger",
            unit_price=Decimal("12.50"),
            quantity=2,
        )
        assert item.order == order
        assert item.item_name == "Test Burger"
        assert item.quantity == 2
        assert item.total_price == Decimal("25.00")

    def test_order_item_str(self, order_item):
        """Test order item string representation."""
        assert str(order_item.quantity) in str(order_item)

    def test_recalculate_total(self, order_item):
        """Test recalculating item total."""
        order_item.quantity = 3
        order_item.recalculate_total()
        assert order_item.total_price == order_item.unit_price * 3


@pytest.mark.django_db
class TestOrderItemModifierModel:
    """Tests for OrderItemModifier model."""

    def test_create_modifier(self, order_item, modifier_group):
        """Test creating an order item modifier."""
        from apps.orders.models import OrderItemModifier

        modifier = OrderItemModifier.objects.create(
            order_item=order_item,
            modifier_name="Extra Cheese",
            price_adjustment=Decimal("1.50"),
        )
        assert modifier.modifier_name == "Extra Cheese"
        assert modifier.price_adjustment == Decimal("1.50")


@pytest.mark.django_db
class TestOrderStatusHistoryModel:
    """Tests for OrderStatusHistory model."""

    def test_create_status_history(self, order, user):
        """Test creating order status history."""
        from apps.orders.models import OrderStatusHistory

        history = OrderStatusHistory.objects.create(
            order=order,
            from_status="pending",
            to_status="confirmed",
            changed_by=user,
            notes="Order confirmed by staff",
        )
        assert history.from_status == "pending"
        assert history.to_status == "confirmed"
        assert history.changed_by == user

    def test_status_history_str(self, order, user):
        """Test status history string representation."""
        from apps.orders.models import OrderStatusHistory

        history = OrderStatusHistory.objects.create(
            order=order,
            from_status="pending",
            to_status="confirmed",
        )
        assert "pending" in str(history)
        assert "confirmed" in str(history)
