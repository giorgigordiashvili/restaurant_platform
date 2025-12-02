"""
Views for payments app.
"""

from decimal import Decimal

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import IsTenantManager

from .models import Payment, PaymentMethod, Refund
from .serializers import (
    CashPaymentSerializer,
    PaymentCreateSerializer,
    PaymentListSerializer,
    PaymentMethodCreateSerializer,
    PaymentMethodSerializer,
    PaymentSerializer,
    RefundCreateSerializer,
    RefundSerializer,
)

# ============== Dashboard Views ==============


@extend_schema(tags=["Dashboard - Payments"])
class PaymentListView(generics.ListAPIView):
    """List payments for a restaurant."""

    serializer_class = PaymentListSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def get_queryset(self):
        queryset = (
            Payment.objects.filter(order__restaurant=self.request.restaurant)
            .select_related("order")
            .order_by("-created_at")
        )

        # Filter by status
        payment_status = self.request.query_params.get("status")
        if payment_status:
            queryset = queryset.filter(status=payment_status)

        # Filter by payment method
        method = self.request.query_params.get("method")
        if method:
            queryset = queryset.filter(payment_method=method)

        # Filter by date
        date = self.request.query_params.get("date")
        if date:
            queryset = queryset.filter(created_at__date=date)

        # Filter by order
        order_id = self.request.query_params.get("order")
        if order_id:
            queryset = queryset.filter(order_id=order_id)

        return queryset


@extend_schema(tags=["Dashboard - Payments"])
class PaymentDetailView(generics.RetrieveAPIView):
    """Get payment details."""

    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]
    lookup_field = "id"

    @require_restaurant
    def get_queryset(self):
        return Payment.objects.filter(order__restaurant=self.request.restaurant).select_related("order")


@extend_schema(tags=["Dashboard - Payments"])
class CashPaymentCreateView(APIView):
    """Record a cash payment."""

    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def post(self, request):
        serializer = CashPaymentSerializer(
            data=request.data,
            context={"restaurant": request.restaurant},
        )
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        order = data["order_id"]

        # Create payment
        payment = Payment.objects.create(
            order=order,
            customer=order.customer,
            processed_by=request.user,
            amount=data["amount"],
            tip_amount=data.get("tip_amount", Decimal("0")),
            total_amount=data["amount"] + data.get("tip_amount", Decimal("0")),
            payment_method="cash",
            status="completed",
            notes=data.get("notes", ""),
        )

        # Complete the payment
        payment.complete()

        # Calculate change
        change = data["amount_received"] - payment.total_amount

        return Response(
            {
                "success": True,
                "message": "Cash payment recorded.",
                "data": {
                    "payment": PaymentSerializer(payment).data,
                    "amount_received": str(data["amount_received"]),
                    "change": str(change),
                },
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Dashboard - Payments"])
class CardPaymentCreateView(APIView):
    """Process a card payment (for POS terminals)."""

    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def post(self, request):
        serializer = PaymentCreateSerializer(
            data=request.data,
            context={"restaurant": request.restaurant},
        )
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        order = data["order_id"]

        # Create payment record
        payment = Payment.objects.create(
            order=order,
            customer=order.customer,
            processed_by=request.user,
            amount=data["amount"],
            tip_amount=data.get("tip_amount", Decimal("0")),
            total_amount=data["amount"] + data.get("tip_amount", Decimal("0")),
            payment_method="card",
            status="pending",
            notes=data.get("notes", ""),
        )

        # In a real implementation, this would integrate with Stripe
        # For now, we'll just mark it as completed
        payment.complete()

        return Response(
            {
                "success": True,
                "message": "Card payment processed.",
                "data": PaymentSerializer(payment).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Dashboard - Payments"])
class RefundListView(generics.ListAPIView):
    """List refunds for a restaurant."""

    serializer_class = RefundSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def get_queryset(self):
        queryset = (
            Refund.objects.filter(payment__order__restaurant=self.request.restaurant)
            .select_related("payment")
            .order_by("-created_at")
        )

        # Filter by status
        refund_status = self.request.query_params.get("status")
        if refund_status:
            queryset = queryset.filter(status=refund_status)

        # Filter by payment
        payment_id = self.request.query_params.get("payment")
        if payment_id:
            queryset = queryset.filter(payment_id=payment_id)

        return queryset


@extend_schema(tags=["Dashboard - Payments"])
class RefundCreateView(APIView):
    """Create a refund."""

    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def post(self, request):
        serializer = RefundCreateSerializer(
            data=request.data,
            context={"restaurant": request.restaurant},
        )
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        payment = data["payment_id"]

        # Create refund
        refund = Refund.objects.create(
            payment=payment,
            processed_by=request.user,
            amount=data["amount"],
            reason=data["reason"],
            reason_details=data.get("reason_details", ""),
            status="pending",
        )

        # In a real implementation, this would process the refund via Stripe
        # For now, we'll just complete it
        refund.complete()

        return Response(
            {
                "success": True,
                "message": "Refund processed.",
                "data": RefundSerializer(refund).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Dashboard - Payments"])
class PaymentStatsView(APIView):
    """Get payment statistics for dashboard."""

    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def get(self, request):
        from django.db.models import Count, Sum
        from django.utils import timezone

        today = timezone.localdate()
        queryset = Payment.objects.filter(
            order__restaurant=request.restaurant,
            status="completed",
        )

        # Today's stats
        today_stats = queryset.filter(created_at__date=today).aggregate(
            total_amount=Sum("total_amount"),
            total_tips=Sum("tip_amount"),
            count=Count("id"),
        )

        # By payment method
        by_method = (
            queryset.filter(created_at__date=today)
            .values("payment_method")
            .annotate(total=Sum("total_amount"), count=Count("id"))
        )

        return Response(
            {
                "success": True,
                "data": {
                    "today": {
                        "total_amount": str(today_stats["total_amount"] or 0),
                        "total_tips": str(today_stats["total_tips"] or 0),
                        "transaction_count": today_stats["count"] or 0,
                    },
                    "by_payment_method": list(by_method),
                },
            }
        )


# ============== Customer Views ==============


@extend_schema(tags=["Payments"])
class CustomerPaymentMethodListView(generics.ListAPIView):
    """List customer's saved payment methods."""

    serializer_class = PaymentMethodSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PaymentMethod.objects.filter(
            customer=self.request.user,
            is_active=True,
        )


@extend_schema(tags=["Payments"])
class CustomerPaymentMethodCreateView(APIView):
    """Add a new payment method."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PaymentMethodCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # In a real implementation, this would retrieve the payment method
        # details from Stripe and store them
        # For now, we'll create a placeholder
        payment_method = PaymentMethod.objects.create(
            customer=request.user,
            method_type="card",
            external_method_id=data["payment_method_id"],
            is_default=data.get("set_as_default", False),
            # These would come from Stripe
            card_brand="visa",
            card_last4="4242",
            card_exp_month=12,
            card_exp_year=2025,
        )

        return Response(
            {
                "success": True,
                "message": "Payment method added.",
                "data": PaymentMethodSerializer(payment_method).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Payments"])
class CustomerPaymentMethodDetailView(APIView):
    """Update or delete a payment method."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, id):
        """Set payment method as default."""
        try:
            payment_method = PaymentMethod.objects.get(
                id=id,
                customer=request.user,
                is_active=True,
            )
        except PaymentMethod.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Payment method not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.data.get("is_default"):
            payment_method.set_as_default()

        return Response(
            {
                "success": True,
                "message": "Payment method updated.",
                "data": PaymentMethodSerializer(payment_method).data,
            }
        )

    def delete(self, request, id):
        """Remove a payment method."""
        try:
            payment_method = PaymentMethod.objects.get(
                id=id,
                customer=request.user,
                is_active=True,
            )
        except PaymentMethod.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Payment method not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        payment_method.deactivate()

        return Response(
            {
                "success": True,
                "message": "Payment method removed.",
            }
        )


@extend_schema(tags=["Payments"])
class CustomerPaymentHistoryView(generics.ListAPIView):
    """Get customer's payment history."""

    serializer_class = PaymentListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Payment.objects.filter(customer=self.request.user).select_related("order").order_by("-created_at")
