"""Dashboard (POS) API for printers and print jobs."""

from django.shortcuts import get_object_or_404

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired, staff_can

from . import services
from .models import Printer, PrintJob
from .serializers import PrinterSerializer, PrinterSetupSerializer, PrintJobCreateSerializer, PrintJobSerializer

TAG = "Dashboard - Printing"


class _MethodPermission:
    """orders:read for GET (the POS shows printer status to every role), settings:update otherwise."""

    def get_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            self.required_permission = ("orders", "read")
        else:
            self.required_permission = ("settings", "update")
        return super().get_permissions()


@extend_schema(tags=[TAG])
class PrinterListCreateView(_MethodPermission, generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("printing")]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return PrinterSetupSerializer
        return PrinterSerializer

    @require_restaurant
    def get_queryset(self):
        return Printer.objects.filter(restaurant=self.request.restaurant)

    @require_restaurant
    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.restaurant)


@extend_schema(tags=[TAG])
class PrinterDetailView(_MethodPermission, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("printing")]
    lookup_field = "id"

    def get_serializer_class(self):
        if staff_can(self.request, "settings", "update"):
            return PrinterSetupSerializer
        return PrinterSerializer

    @require_restaurant
    def get_queryset(self):
        return Printer.objects.filter(restaurant=self.request.restaurant)


@extend_schema(tags=[TAG], request=None, responses={200: PrinterSetupSerializer})
class PrinterRotateKeyView(APIView):
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("printing")]
    required_permission = ("settings", "update")

    @require_restaurant
    def post(self, request, id):
        printer = get_object_or_404(Printer, id=id, restaurant=request.restaurant)
        printer.rotate_key()
        return Response({"success": True, "data": PrinterSetupSerializer(printer).data})


@extend_schema(tags=[TAG], request=None, responses={201: PrintJobSerializer})
class PrinterTestView(APIView):
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("printing")]
    required_permission = ("settings", "update")

    @require_restaurant
    def post(self, request, id):
        printer = get_object_or_404(Printer, id=id, restaurant=request.restaurant)
        job = services.enqueue_test(printer, by=request.user)
        return Response({"success": True, "data": PrintJobSerializer(job).data}, status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG])
class PrintJobListView(generics.ListAPIView):
    serializer_class = PrintJobSerializer
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("printing")]
    required_permission = ("orders", "read")

    @require_restaurant
    def get_queryset(self):
        qs = PrintJob.objects.filter(restaurant=self.request.restaurant).select_related("printer", "order")
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("printer"):
            qs = qs.filter(printer_id=params["printer"])
        if params.get("order"):
            qs = qs.filter(order_id=params["order"])
        return qs.order_by("-created_at")


@extend_schema(tags=[TAG], request=PrintJobCreateSerializer, responses={201: PrintJobSerializer(many=True)})
class PrintJobCreateView(APIView):
    """Print (or re-print) a kitchen ticket, a receipt or a shift report."""

    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("printing")]
    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request):
        serializer = PrintJobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        restaurant = request.restaurant
        printers = None
        if data.get("printer_id"):
            printers = Printer.objects.filter(
                id=data["printer_id"], restaurant=restaurant, is_active=True, connection="bridge"
            )
        jobs = []
        if data["kind"] == "ticket":
            from apps.orders.models import Order

            order = get_object_or_404(Order, id=data["order_id"], restaurant=restaurant)
            if printers is None:
                printers = services.bridge_printers(restaurant)
                if data.get("station"):
                    printers = [p for p in printers if p.serves(data["station"])]
            jobs = services.enqueue_ticket(order, reason="reprint", by=request.user, printers=printers)
        elif data["kind"] == "receipt":
            from apps.orders.models import Order
            from apps.payments.models import Payment

            payment = None
            if data.get("payment_id"):
                payment = get_object_or_404(Payment, id=data["payment_id"], restaurant=restaurant)
                order = payment.order
            else:
                order = get_object_or_404(Order, id=data["order_id"], restaurant=restaurant)
            if printers is None:
                printers = services.bridge_printers(restaurant, kinds=("receipt",))
            jobs = services.enqueue_receipt(order, payment=payment, by=request.user, printers=printers)
        else:
            from apps.payments.models import CashShift

            shift = get_object_or_404(CashShift, id=data["shift_id"], restaurant=restaurant)
            if printers is None:
                printers = services.bridge_printers(restaurant, kinds=("receipt",))
            jobs = services.enqueue_report(shift, by=request.user, printers=printers)
        if not jobs:
            return Response(
                {"success": False, "error": {"code": "no_printer", "message": "No active printer for this document."}},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {"success": True, "data": PrintJobSerializer(jobs, many=True).data}, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=[TAG], request=None, responses={200: PrintJobSerializer})
class PrintJobRetryView(APIView):
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("printing")]
    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request, id):
        job = get_object_or_404(PrintJob, id=id, restaurant=request.restaurant)
        services.retry(job)
        return Response({"success": True, "data": PrintJobSerializer(job).data})
