"""
Views for payments app.

Dashboard (POS) endpoints are role-based on the ``cash`` resource:
read = see shift / X report / payments, create = take payments & open a
shift & paid in/out, update = close a shift, delete = refunds. Everything
that moves money goes through ``apps.payments.services``.
"""

from decimal import Decimal

from django.shortcuts import get_object_or_404

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired, staff_can

from . import services
from .models import CashShift, DiscountReason, Payment, PaymentMethod, Refund
from .serializers import (
    CashMovementCreateSerializer,
    CashMovementSerializer,
    CashPaymentSerializer,
    CashShiftSerializer,
    CloseShiftSerializer,
    OpenShiftSerializer,
    PaymentCreateSerializer,
    PaymentListSerializer,
    PaymentMethodCreateSerializer,
    PaymentMethodSerializer,
    PaymentRefundSerializer,
    PaymentSerializer,
    ReasonOptionSerializer,
    RecordPaymentResponseSerializer,
    RecordPaymentSerializer,
    RefundCreateSerializer,
    RefundSerializer,
    SplitEvenResponseSerializer,
    SplitEvenSerializer,
)


def _ledger_error(exc: services.LedgerError, http_status=status.HTTP_409_CONFLICT):
    return Response({"success": False, "error": exc.as_dict()}, status=http_status)


def _payment_response(payment, *, message="Payment recorded.", http_status=status.HTTP_201_CREATED):
    orders = [a.order for a in payment.allocations.select_related("order")]
    return Response(
        {
            "success": True,
            "message": message,
            "data": {
                "payment": PaymentSerializer(payment).data,
                "change": str(payment.change_given or Decimal("0")),
                "receipt_number": payment.receipt_number,
                "balance": str(sum((services.balance(o) for o in orders), Decimal("0"))),
                "paid_order_numbers": [o.order_number for o in orders if services.is_paid(o)],
            },
        },
        status=http_status,
    )


# ============== Dashboard: payments ==============


@extend_schema(tags=["Dashboard - Payments"])
class PaymentListView(generics.ListAPIView):
    """List payments for a restaurant."""

    serializer_class = PaymentListSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("cash", "read")

    @require_restaurant
    def get_queryset(self):
        queryset = (
            Payment.objects.filter(restaurant=self.request.restaurant).select_related("order").order_by("-created_at")
        )
        params = self.request.query_params
        if params.get("status"):
            queryset = queryset.filter(status=params["status"])
        if params.get("method"):
            queryset = queryset.filter(payment_method=params["method"])
        if params.get("date"):
            queryset = queryset.filter(created_at__date=params["date"])
        if params.get("order"):
            queryset = queryset.filter(allocations__order_id=params["order"]).distinct()
        if params.get("session"):
            queryset = queryset.filter(session_id=params["session"])
        if params.get("shift"):
            queryset = queryset.filter(shift_id=params["shift"])
        return queryset


@extend_schema(tags=["Dashboard - Payments"])
class PaymentDetailView(generics.RetrieveAPIView):
    """Get payment details."""

    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("cash", "read")
    lookup_field = "id"

    @require_restaurant
    def get_queryset(self):
        return Payment.objects.filter(restaurant=self.request.restaurant).select_related("order")


@extend_schema(
    tags=["Dashboard - Payments"], request=RecordPaymentSerializer, responses={201: RecordPaymentResponseSerializer}
)
class RecordPaymentView(APIView):
    """
    Take a payment at the POS (cash / card terminal / voucher / other) for
    one order, several orders or a whole table session. Partial amounts are
    allowed; overpaying is refused (409 ``overpay`` / ``already_paid``);
    cash needs an open shift (409 ``shift_required``).
    """

    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("cash")]
    required_permission = ("cash", "create")

    @require_restaurant
    def post(self, request):
        serializer = RecordPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        from apps.orders.models import Order
        from apps.tables.models import TableSession

        kwargs = {}
        if data.get("order_id"):
            kwargs["order"] = get_object_or_404(Order, id=data["order_id"], restaurant=request.restaurant)
        elif data.get("order_ids"):
            orders = list(Order.objects.filter(id__in=data["order_ids"], restaurant=request.restaurant))
            if len(orders) != len(set(data["order_ids"])):
                return Response(
                    {"success": False, "error": {"code": "order_not_found", "message": "Order not found."}},
                    status=status.HTTP_404_NOT_FOUND,
                )
            kwargs["orders"] = orders
        else:
            kwargs["session"] = get_object_or_404(
                TableSession, id=data["session_id"], table__restaurant=request.restaurant
            )
        try:
            payment = services.record_payment(
                request.restaurant,
                method=data["method"],
                amount=data["amount"],
                tip=data.get("tip_amount") or Decimal("0"),
                tendered=data.get("tendered"),
                by=request.user,
                notes=data.get("notes", ""),
                request=request,
                **kwargs,
            )
        except services.LedgerError as exc:
            return _ledger_error(exc)
        return _payment_response(payment)


@extend_schema(tags=["Dashboard - Payments"], request=SplitEvenSerializer, responses={200: SplitEvenResponseSerializer})
class SplitEvenView(APIView):
    """Preview an even split: shares that add up exactly to the balance."""

    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("cash")]
    required_permission = ("cash", "read")

    @require_restaurant
    def post(self, request):
        serializer = SplitEvenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        from apps.orders.models import Order
        from apps.tables.models import TableSession

        if data.get("amount") is not None:
            total = data["amount"]
        elif data.get("order_id"):
            order = get_object_or_404(Order, id=data["order_id"], restaurant=request.restaurant)
            total = services.balance(order)
        else:
            session = get_object_or_404(TableSession, id=data["session_id"], table__restaurant=request.restaurant)
            total = services.session_balance(session)
        try:
            shares = services.split_evenly(total, data["ways"])
        except services.LedgerError as exc:
            return _ledger_error(exc, status.HTTP_400_BAD_REQUEST)
        return Response(
            {"success": True, "data": {"total": str(total), "ways": data["ways"], "shares": [str(s) for s in shares]}}
        )


@extend_schema(tags=["Dashboard - Payments"], request=PaymentRefundSerializer, responses={201: RefundSerializer})
class PaymentRefundView(APIView):
    """Refund (part of) a payment."""

    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("cash", "delete")

    @require_restaurant
    def post(self, request, id):
        payment = get_object_or_404(Payment, id=id, restaurant=request.restaurant)
        serializer = PaymentRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        reason_code = None
        if data.get("reason_id"):
            reason_code = get_object_or_404(DiscountReason, id=data["reason_id"], restaurant=request.restaurant)
        order = None
        if data.get("order_id"):
            from apps.orders.models import Order

            order = get_object_or_404(Order, id=data["order_id"], restaurant=request.restaurant)
        try:
            refund = services.refund_payment(
                payment,
                amount=data["amount"],
                by=request.user,
                method=data.get("method"),
                reason=data.get("reason", "customer_request"),
                reason_details=data.get("reason_details", ""),
                reason_code=reason_code,
                order=order,
                request=request,
            )
        except services.LedgerError as exc:
            return _ledger_error(exc)
        return Response(
            {"success": True, "message": "Refund recorded.", "data": RefundSerializer(refund).data},
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Dashboard - Payments"], responses={200: ReasonOptionSerializer(many=True)})
class DiscountReasonListView(APIView):
    """Reasons for ``?kind=discount|comp|void|refund`` (the restaurant's own, or built-in defaults)."""

    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("orders", "read")

    @require_restaurant
    def get(self, request):
        kind = request.query_params.get("kind", "discount")
        if kind not in dict(DiscountReason.KIND_CHOICES):
            return Response(
                {"success": False, "error": {"message": "Unknown kind."}}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response({"success": True, "data": services.reasons_for(request.restaurant, kind)})


# ============== Dashboard: shifts ==============


class _ShiftBase(APIView):
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("cash")]

    def _shift(self, request, id):
        return get_object_or_404(CashShift, id=id, restaurant=request.restaurant)


@extend_schema(tags=["Dashboard - Cash shifts"], request=OpenShiftSerializer, responses={201: CashShiftSerializer})
class ShiftOpenView(_ShiftBase):
    required_permission = ("cash", "create")

    @require_restaurant
    def post(self, request):
        serializer = OpenShiftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            shift = services.open_shift(
                request.restaurant,
                by=request.user,
                opening_float=data.get("opening_float") or Decimal("0"),
                register=data.get("register", ""),
                notes=data.get("notes", ""),
            )
        except services.LedgerError as exc:
            return _ledger_error(exc)
        return Response(
            {"success": True, "message": "Shift opened.", "data": CashShiftSerializer(shift).data},
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Dashboard - Cash shifts"], responses={200: CashShiftSerializer})
class ShiftCurrentView(_ShiftBase):
    """The open shift, or ``data: null``."""

    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request):
        shift = services.current_shift(request.restaurant)
        return Response({"success": True, "data": CashShiftSerializer(shift).data if shift else None})


@extend_schema(tags=["Dashboard - Cash shifts"])
class ShiftXReportView(_ShiftBase):
    """Live figures of the open shift (what the Z report will say if closed now)."""

    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request):
        shift = services.current_shift(request.restaurant)
        if shift is None:
            return Response(
                {"success": False, "error": {"code": "no_open_shift", "message": "No open shift."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {"success": True, "data": {"shift": CashShiftSerializer(shift).data, "report": services.x_report(shift)}}
        )


@extend_schema(tags=["Dashboard - Cash shifts"], request=CloseShiftSerializer, responses={200: CashShiftSerializer})
class ShiftCloseView(_ShiftBase):
    required_permission = ("cash", "update")

    @require_restaurant
    def post(self, request, id):
        shift = self._shift(request, id)
        serializer = CloseShiftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            shift = services.close_shift(
                shift, by=request.user, counted_cash=data["counted_cash"], notes=data.get("notes", "")
            )
        except services.LedgerError as exc:
            return _ledger_error(exc)
        return Response({"success": True, "message": "Shift closed.", "data": CashShiftSerializer(shift).data})


@extend_schema(
    tags=["Dashboard - Cash shifts"],
    request=CashMovementCreateSerializer,
    responses={200: CashMovementSerializer(many=True)},
)
class ShiftMovementsView(_ShiftBase):
    """GET the paid in / paid out rows of a shift; POST a new one."""

    def get_permissions(self):
        self.required_permission = ("cash", "create" if self.request.method == "POST" else "read")
        return super().get_permissions()

    @require_restaurant
    def get(self, request, id):
        shift = self._shift(request, id)
        return Response({"success": True, "data": CashMovementSerializer(shift.movements.all(), many=True).data})

    @require_restaurant
    def post(self, request, id):
        shift = self._shift(request, id)
        serializer = CashMovementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            movement = services.add_movement(
                shift, kind=data["kind"], amount=data["amount"], reason=data["reason"], by=request.user
            )
        except services.LedgerError as exc:
            return _ledger_error(exc)
        return Response(
            {"success": True, "data": CashMovementSerializer(movement).data}, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["Dashboard - Cash shifts"])
class ShiftListView(generics.ListAPIView):
    serializer_class = CashShiftSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("cash")]
    required_permission = ("cash", "read")

    @require_restaurant
    def get_queryset(self):
        qs = CashShift.objects.filter(restaurant=self.request.restaurant).select_related("opened_by", "closed_by")
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs.order_by("-opened_at")


@extend_schema(tags=["Dashboard - Cash shifts"])
class ShiftDetailView(generics.RetrieveAPIView):
    serializer_class = CashShiftSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("cash")]
    required_permission = ("cash", "read")
    lookup_field = "id"

    @require_restaurant
    def get_queryset(self):
        return CashShift.objects.filter(restaurant=self.request.restaurant)


# ============== Legacy dashboard endpoints (thin wrappers) ==============


@extend_schema(tags=["Dashboard - Payments"], request=CashPaymentSerializer)
class CashPaymentCreateView(APIView):
    """Record a cash payment for one order (legacy shape; prefer ``record/``)."""

    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("cash", "create")

    @require_restaurant
    def post(self, request):
        serializer = CashPaymentSerializer(data=request.data, context={"restaurant": request.restaurant})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            payment = services.record_payment(
                request.restaurant,
                method="cash",
                amount=data["amount"],
                tip=data.get("tip_amount", Decimal("0")),
                tendered=data["amount_received"],
                order=data["order_id"],
                by=request.user,
                notes=data.get("notes", ""),
                request=request,
            )
        except services.LedgerError as exc:
            return _ledger_error(exc)
        return Response(
            {
                "success": True,
                "message": "Cash payment recorded.",
                "data": {
                    "payment": PaymentSerializer(payment).data,
                    "amount_received": str(data["amount_received"]),
                    "change": str(payment.change_given),
                },
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Dashboard - Payments"], request=PaymentCreateSerializer)
class CardPaymentCreateView(APIView):
    """Record a card-terminal payment for one order (legacy shape; prefer ``record/``)."""

    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("cash", "create")

    @require_restaurant
    def post(self, request):
        serializer = PaymentCreateSerializer(data=request.data, context={"restaurant": request.restaurant})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            payment = services.record_payment(
                request.restaurant,
                method="card_terminal",
                amount=data["amount"],
                tip=data.get("tip_amount", Decimal("0")),
                order=data["order_id"],
                by=request.user,
                notes=data.get("notes", ""),
                request=request,
            )
        except services.LedgerError as exc:
            return _ledger_error(exc)
        return Response(
            {"success": True, "message": "Card payment processed.", "data": PaymentSerializer(payment).data},
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Dashboard - Payments"])
class RefundListView(generics.ListAPIView):
    """List refunds for a restaurant."""

    serializer_class = RefundSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("cash", "read")

    @require_restaurant
    def get_queryset(self):
        queryset = (
            Refund.objects.filter(restaurant=self.request.restaurant).select_related("payment").order_by("-created_at")
        )
        if self.request.query_params.get("status"):
            queryset = queryset.filter(status=self.request.query_params["status"])
        if self.request.query_params.get("payment"):
            queryset = queryset.filter(payment_id=self.request.query_params["payment"])
        return queryset


@extend_schema(tags=["Dashboard - Payments"], request=RefundCreateSerializer)
class RefundCreateView(APIView):
    """Create a refund (legacy shape; prefer ``<payment>/refund/``)."""

    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("cash", "delete")

    @require_restaurant
    def post(self, request):
        serializer = RefundCreateSerializer(data=request.data, context={"restaurant": request.restaurant})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            refund = services.refund_payment(
                data["payment_id"],
                amount=data["amount"],
                by=request.user,
                reason=data["reason"],
                reason_details=data.get("reason_details", ""),
                request=request,
            )
        except services.LedgerError as exc:
            return _ledger_error(exc)
        return Response(
            {"success": True, "message": "Refund processed.", "data": RefundSerializer(refund).data},
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Dashboard - Payments"])
class PaymentStatsView(APIView):
    """Get payment statistics for dashboard."""

    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request):
        from django.db.models import Count, Sum
        from django.utils import timezone

        today = timezone.localdate()
        queryset = Payment.objects.filter(restaurant=request.restaurant, status="completed")
        today_stats = queryset.filter(created_at__date=today).aggregate(
            total_amount=Sum("total_amount"), total_tips=Sum("tip_amount"), count=Count("id")
        )
        by_method = (
            queryset.filter(created_at__date=today)
            .values("payment_method")
            .annotate(total=Sum("total_amount"), count=Count("id"))
        )
        shift = services.current_shift(request.restaurant)
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
                    "open_shift": CashShiftSerializer(shift).data if shift else None,
                    "can_close_shift": staff_can(request, "cash", "update"),
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
        return PaymentMethod.objects.filter(customer=self.request.user, is_active=True)


@extend_schema(tags=["Payments"])
class CustomerPaymentMethodCreateView(APIView):
    """Add a new payment method."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PaymentMethodCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        payment_method = PaymentMethod.objects.create(
            customer=request.user,
            method_type="card",
            external_method_id=data["payment_method_id"],
            is_default=data.get("set_as_default", False),
            card_brand="visa",
            card_last4="4242",
            card_exp_month=12,
            card_exp_year=2025,
        )
        return Response(
            {"success": True, "message": "Payment method added.", "data": PaymentMethodSerializer(payment_method).data},
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Payments"])
class CustomerPaymentMethodDetailView(APIView):
    """Update or delete a payment method."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, id):
        """Set payment method as default."""
        try:
            payment_method = PaymentMethod.objects.get(id=id, customer=request.user, is_active=True)
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
            payment_method = PaymentMethod.objects.get(id=id, customer=request.user, is_active=True)
        except PaymentMethod.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Payment method not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        payment_method.deactivate()
        return Response({"success": True, "message": "Payment method removed."})


@extend_schema(tags=["Payments"])
class CustomerPaymentHistoryView(generics.ListAPIView):
    """Get customer's payment history."""

    serializer_class = PaymentListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Payment.objects.filter(customer=self.request.user).select_related("order").order_by("-created_at")
