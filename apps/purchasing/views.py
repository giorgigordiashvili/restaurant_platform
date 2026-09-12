"""Dashboard API: suppliers, purchase orders, receiving (for the POS / SPA later)."""

from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired
from apps.purchasing import services
from apps.purchasing.models import PurchaseOrder, Supplier
from apps.purchasing.serializers import PurchaseOrderSerializer, ReceiveSerializer, SupplierSerializer

TAG = "Dashboard - Purchasing"
PERMS = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("purchasing")]


@extend_schema(tags=[TAG])
class SupplierListView(generics.ListAPIView):
    permission_classes = PERMS
    required_permission = ("warehouse", "read")
    serializer_class = SupplierSerializer

    @require_restaurant
    def get_queryset(self):
        return Supplier.objects.filter(restaurant=self.request.restaurant, is_active=True)


@extend_schema(tags=[TAG])
class PurchaseOrderListView(generics.ListAPIView):
    permission_classes = PERMS
    required_permission = ("warehouse", "read")
    serializer_class = PurchaseOrderSerializer

    @require_restaurant
    def get_queryset(self):
        qs = (
            PurchaseOrder.objects.filter(restaurant=self.request.restaurant)
            .select_related("supplier")
            .prefetch_related("lines__stock_item", "lines__unit")
        )
        st = self.request.query_params.get("status")
        if st == "open":
            qs = qs.filter(status__in=PurchaseOrder.OPEN)
        elif st:
            qs = qs.filter(status=st)
        return qs


@extend_schema(tags=[TAG], responses=PurchaseOrderSerializer)
class PurchaseOrderDetailView(APIView):
    permission_classes = PERMS
    required_permission = ("warehouse", "read")

    @require_restaurant
    def get(self, request, order_id):
        po = get_object_or_404(PurchaseOrder, pk=order_id, restaurant=request.restaurant)
        return Response(PurchaseOrderSerializer(po).data)


@extend_schema(tags=[TAG], request=ReceiveSerializer, responses=PurchaseOrderSerializer)
class PurchaseOrderReceiveView(APIView):
    permission_classes = PERMS
    required_permission = ("warehouse", "create")

    @require_restaurant
    def post(self, request, order_id):
        po = get_object_or_404(PurchaseOrder, pk=order_id, restaurant=request.restaurant)
        ser = ReceiveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            services.receive_order(
                po, ser.validated_data["lines"], by=request.user, reference=ser.validated_data.get("reference", "")
            )
        except services.PurchasingError as exc:
            return Response(
                {"success": False, "error": {"code": exc.code, "message": exc.message}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        po.refresh_from_db()
        return Response(PurchaseOrderSerializer(po).data)


@extend_schema(tags=[TAG], request=None, responses=PurchaseOrderSerializer(many=True))
class FromBuyListView(APIView):
    permission_classes = PERMS
    required_permission = ("warehouse", "create")

    @require_restaurant
    def post(self, request):
        orders = services.orders_from_buy_list(request.restaurant, by=request.user)
        return Response(PurchaseOrderSerializer(orders, many=True).data, status=status.HTTP_201_CREATED)
