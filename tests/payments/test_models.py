"""
Tests for payments models.
"""

from decimal import Decimal

import pytest


@pytest.mark.django_db
class TestPaymentModel:
    """Tests for Payment model."""

    def test_create_payment(self, create_payment, order):
        """Test creating a payment."""
        payment = create_payment(
            order=order,
            amount=Decimal("100.00"),
            tip_amount=Decimal("15.00"),
        )
        assert payment.amount == Decimal("100.00")
        assert payment.tip_amount == Decimal("15.00")
        assert payment.total_amount == Decimal("115.00")
        assert payment.status == "pending"

    def test_payment_str(self, payment):
        """Test payment string representation."""
        assert str(payment.total_amount) in str(payment)

    def test_complete_payment(self, payment):
        """Test completing a payment."""
        payment.status = "pending"
        payment.save()
        payment.complete()
        assert payment.status == "completed"
        assert payment.completed_at is not None
        assert payment.receipt_number != ""

    def test_fail_payment(self, create_payment, order):
        """Test failing a payment."""
        payment = create_payment(order=order, status="pending")
        payment.fail(reason="Card declined")
        assert payment.status == "failed"
        assert payment.failed_at is not None
        assert payment.failure_reason == "Card declined"

    def test_cancel_payment(self, create_payment, order):
        """Test cancelling a payment."""
        payment = create_payment(order=order, status="pending")
        payment.cancel()
        assert payment.status == "cancelled"

    def test_is_refundable(self, payment):
        """Test is_refundable property."""
        # Completed payments are refundable
        payment.status = "completed"
        assert payment.is_refundable is True

        # Pending payments are not refundable
        payment.status = "pending"
        assert payment.is_refundable is False

    def test_refundable_amount(self, payment):
        """Test refundable_amount property."""
        assert payment.refundable_amount == payment.total_amount

    def test_generate_receipt_number(self, payment):
        """Test receipt number generation."""
        payment.status = "pending"
        payment.receipt_number = ""
        payment.save()
        payment.complete()
        assert payment.receipt_number.startswith("RCP-")


@pytest.mark.django_db
class TestRefundModel:
    """Tests for Refund model."""

    def test_create_refund(self, create_refund, payment):
        """Test creating a refund."""
        refund = create_refund(
            payment=payment,
            amount=Decimal("25.00"),
            reason="quality_issue",
        )
        assert refund.amount == Decimal("25.00")
        assert refund.reason == "quality_issue"
        assert refund.status == "pending"

    def test_refund_str(self, refund):
        """Test refund string representation."""
        assert str(refund.amount) in str(refund)

    def test_complete_refund(self, refund):
        """Test completing a refund."""
        refund.complete()
        assert refund.status == "completed"
        assert refund.completed_at is not None

    def test_complete_refund_updates_payment_status(self, create_refund, payment):
        """Test that completing a full refund updates payment status."""
        refund = create_refund(payment=payment, amount=payment.total_amount)
        refund.complete()
        payment.refresh_from_db()
        assert payment.status == "refunded"

    def test_partial_refund_updates_payment_status(self, create_refund, payment):
        """Test that completing a partial refund updates payment status."""
        refund = create_refund(payment=payment, amount=Decimal("10.00"))
        refund.complete()
        payment.refresh_from_db()
        assert payment.status == "partially_refunded"

    def test_fail_refund(self, refund):
        """Test failing a refund."""
        refund.fail(reason="External error")
        assert refund.status == "failed"
        assert refund.failed_at is not None
        assert refund.failure_reason == "External error"


@pytest.mark.django_db
class TestPaymentMethodModel:
    """Tests for PaymentMethod model."""

    def test_create_payment_method(self, create_payment_method, user):
        """Test creating a payment method."""
        method = create_payment_method(customer=user)
        assert method.customer == user
        assert method.method_type == "card"
        assert method.card_last4 == "4242"

    def test_payment_method_str(self, payment_method):
        """Test payment method string representation."""
        assert "4242" in str(payment_method)

    def test_set_as_default(self, create_payment_method, user):
        """Test setting payment method as default."""
        method1 = create_payment_method(customer=user, is_default=True)
        method2 = create_payment_method(
            customer=user,
            external_method_id="pm_test456",
            is_default=False,
        )

        method2.set_as_default()
        method1.refresh_from_db()

        assert method2.is_default is True
        assert method1.is_default is False

    def test_deactivate(self, payment_method):
        """Test deactivating a payment method."""
        payment_method.deactivate()
        assert payment_method.is_active is False

    def test_only_one_default_per_customer(self, create_payment_method, user):
        """Test that only one payment method can be default per customer."""
        method1 = create_payment_method(customer=user, is_default=True)
        method2 = create_payment_method(
            customer=user,
            external_method_id="pm_test789",
            is_default=True,
        )

        method1.refresh_from_db()
        assert method2.is_default is True
        assert method1.is_default is False
