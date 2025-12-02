"""
Views for tables app.
"""

from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import IsTenantManager

from .models import Table, TableQRCode, TableSection, TableSession
from .serializers import (
    QRCodeScanSerializer,
    TableCreateSerializer,
    TableQRCodeSerializer,
    TableSectionSerializer,
    TableSerializer,
    TableSessionCreateSerializer,
    TableSessionSerializer,
)

# ============== Public Views ==============


@extend_schema(tags=["Tables"])
class QRCodeScanView(APIView):
    """Scan a QR code to get table and restaurant info."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = QRCodeScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        code = serializer.validated_data["code"]
        qr = TableQRCode.objects.select_related("table", "table__restaurant").get(
            code=code,
            is_active=True,
        )

        # Record the scan
        qr.record_scan()

        return Response(
            {
                "success": True,
                "data": {
                    "restaurant_name": qr.table.restaurant.name,
                    "restaurant_slug": qr.table.restaurant.slug,
                    "table_number": qr.table.number,
                    "table_name": qr.table.name,
                    "table_id": str(qr.table.id),
                },
            }
        )


# ============== Dashboard Views ==============


@extend_schema(tags=["Dashboard - Tables"])
class TableSectionListCreateView(generics.ListCreateAPIView):
    """List or create table sections."""

    serializer_class = TableSectionSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def get_queryset(self):
        return TableSection.objects.filter(restaurant=self.request.restaurant).order_by("display_order")

    @require_restaurant
    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.restaurant)


@extend_schema(tags=["Dashboard - Tables"])
class TableSectionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Get, update, or delete a table section."""

    serializer_class = TableSectionSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]
    lookup_field = "id"

    @require_restaurant
    def get_queryset(self):
        return TableSection.objects.filter(restaurant=self.request.restaurant)


@extend_schema(tags=["Dashboard - Tables"])
class TableListCreateView(generics.ListCreateAPIView):
    """List or create tables."""

    permission_classes = [IsAuthenticated, IsTenantManager]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return TableCreateSerializer
        return TableSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["restaurant"] = getattr(self.request, "restaurant", None)
        return context

    @require_restaurant
    def get_queryset(self):
        queryset = (
            Table.objects.filter(restaurant=self.request.restaurant)
            .select_related("section")
            .prefetch_related("qr_codes")
            .order_by("section__display_order", "number")
        )

        # Filter by section
        section_id = self.request.query_params.get("section")
        if section_id:
            queryset = queryset.filter(section_id=section_id)

        # Filter by status
        table_status = self.request.query_params.get("status")
        if table_status:
            queryset = queryset.filter(status=table_status)

        # Filter by active
        is_active = self.request.query_params.get("active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")

        return queryset


@extend_schema(tags=["Dashboard - Tables"])
class TableDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Get, update, or delete a table."""

    serializer_class = TableSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]
    lookup_field = "id"

    @require_restaurant
    def get_queryset(self):
        return (
            Table.objects.filter(restaurant=self.request.restaurant)
            .select_related("section")
            .prefetch_related("qr_codes")
        )


@extend_schema(tags=["Dashboard - Tables"])
class TableStatusUpdateView(APIView):
    """Update table status."""

    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def patch(self, request, id):
        try:
            table = Table.objects.get(id=id, restaurant=request.restaurant)
        except Table.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Table not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        new_status = request.data.get("status")
        if new_status not in dict(Table.STATUS_CHOICES):
            return Response(
                {"success": False, "error": {"message": "Invalid status."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        table.status = new_status
        table.save(update_fields=["status", "updated_at"])

        return Response(
            {
                "success": True,
                "message": f"Table status updated to {new_status}.",
                "data": TableSerializer(table).data,
            }
        )


@extend_schema(tags=["Dashboard - Tables"])
class TableQRCodeListCreateView(generics.ListCreateAPIView):
    """List or create QR codes for a table."""

    serializer_class = TableQRCodeSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def get_queryset(self):
        table_id = self.kwargs.get("table_id")
        return TableQRCode.objects.filter(
            table_id=table_id,
            table__restaurant=self.request.restaurant,
        )

    @require_restaurant
    def perform_create(self, serializer):
        table_id = self.kwargs.get("table_id")
        table = Table.objects.get(id=table_id, restaurant=self.request.restaurant)
        serializer.save(table=table)


@extend_schema(tags=["Dashboard - Tables"])
class TableQRCodeDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Get, update, or delete a QR code."""

    serializer_class = TableQRCodeSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]
    lookup_field = "id"

    @require_restaurant
    def get_queryset(self):
        return TableQRCode.objects.filter(table__restaurant=self.request.restaurant)


@extend_schema(tags=["Dashboard - Tables"])
class TableSessionListView(generics.ListAPIView):
    """List table sessions."""

    serializer_class = TableSessionSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def get_queryset(self):
        queryset = (
            TableSession.objects.filter(table__restaurant=self.request.restaurant)
            .select_related("table")
            .order_by("-started_at")
        )

        # Filter by table
        table_id = self.request.query_params.get("table")
        if table_id:
            queryset = queryset.filter(table_id=table_id)

        # Filter by status
        session_status = self.request.query_params.get("status")
        if session_status:
            queryset = queryset.filter(status=session_status)

        return queryset


@extend_schema(tags=["Dashboard - Tables"])
class TableSessionCreateView(APIView):
    """Start a new table session."""

    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def post(self, request):
        serializer = TableSessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # Get table by QR code or ID
        if data.get("qr_code"):
            table = TableQRCode.get_table_by_code(data["qr_code"])
            if not table or table.restaurant != request.restaurant:
                return Response(
                    {"success": False, "error": {"message": "Invalid QR code."}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qr = TableQRCode.objects.get(code=data["qr_code"])
        else:
            try:
                table = Table.objects.get(id=data["table_id"], restaurant=request.restaurant)
                qr = None
            except Table.DoesNotExist:
                return Response(
                    {"success": False, "error": {"message": "Table not found."}},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # Check if table already has active session
        if table.sessions.filter(status="active").exists():
            return Response(
                {"success": False, "error": {"message": "Table already has an active session."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create session
        session = TableSession.objects.create(
            table=table,
            qr_code=qr,
            guest_count=data.get("guest_count", 1),
        )

        # Mark table as occupied
        table.set_occupied()

        return Response(
            {
                "success": True,
                "message": "Session started.",
                "data": TableSessionSerializer(session).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Dashboard - Tables"])
class TableSessionCloseView(APIView):
    """Close a table session."""

    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def post(self, request, id):
        try:
            session = TableSession.objects.get(
                id=id,
                table__restaurant=request.restaurant,
            )
        except TableSession.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Session not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        if session.status == "closed":
            return Response(
                {"success": False, "error": {"message": "Session already closed."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session.close()

        return Response(
            {
                "success": True,
                "message": "Session closed.",
                "data": TableSessionSerializer(session).data,
            }
        )
