"""Dashboard API: the receipt of a payment (fiscal fields included) and the document list."""

from django.shortcuts import get_object_or_404

from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired
from apps.fiscal import services
from apps.fiscal.models import FiscalDocument
from apps.fiscal.serializers import FiscalDocumentSerializer


@extend_schema(tags=["Dashboard - Fiscal"])
class PaymentReceiptView(APIView):
    """The receipt for a payment: the fiscal document when the module is on, otherwise the plain receipt data."""

    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request, id):
        from apps.payments.models import Payment
        from apps.printing.receipts import receipt_data

        payment = get_object_or_404(Payment, id=id, restaurant=request.restaurant)
        doc = FiscalDocument.objects.filter(payment=payment, kind="receipt").exclude(status="cancelled").first()
        if doc is not None:
            data = services.render(doc)
            data["status"] = doc.status
        else:
            data = receipt_data(payment.order, payment=payment) if payment.order_id else {}
            data["status"] = "none"
        return Response({"success": True, "data": data})


@extend_schema(tags=["Dashboard - Fiscal"])
class FiscalDocumentListView(generics.ListAPIView):
    serializer_class = FiscalDocumentSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("fiscal")]
    required_permission = ("fiscal", "read")

    @require_restaurant
    def get_queryset(self):
        qs = FiscalDocument.objects.filter(restaurant=self.request.restaurant)
        params = self.request.query_params
        if params.get("kind"):
            qs = qs.filter(kind=params["kind"])
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        return qs.order_by("-created_at")
