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

from .models import Table, TableQRCode, TableSection, TableSession, TableSessionGuest
from .serializers import (
    JoinSessionSerializer,
    QRCodeScanSerializer,
    SessionInviteResponseSerializer,
    SessionJoinPreviewSerializer,
    TableCreateSerializer,
    TableQRCodeSerializer,
    TableSectionSerializer,
    TableSerializer,
    TableSessionCreateSerializer,
    TableSessionDetailSerializer,
    TableSessionGuestSerializer,
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

        # Create session with host
        session = TableSession.objects.create(
            table=table,
            qr_code=qr,
            host=request.user,
            guest_count=data.get("guest_count", 1),
        )

        # Create host as first guest
        TableSessionGuest.objects.create(
            session=session,
            user=request.user,
            is_host=True,
        )

        # Mark table as occupied
        table.set_occupied()

        return Response(
            {
                "success": True,
                "message": "Session started.",
                "data": TableSessionDetailSerializer(session).data,
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


# ============== Public Session Views (for multi-user ordering) ==============


@extend_schema(tags=["Table Sessions"])
class TableSessionDetailPublicView(generics.RetrieveAPIView):
    """
    Get table session details (for guests at the table).
    Requires the user to be a guest of the session.
    """

    serializer_class = TableSessionDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = "id"

    def get_queryset(self):
        return (
            TableSession.objects.filter(status__in=["active", "payment_pending"])
            .select_related("table", "table__restaurant", "host")
            .prefetch_related("guests", "guests__user")
        )


@extend_schema(tags=["Table Sessions"])
class TableSessionInviteView(APIView):
    """
    Generate or retrieve invite code for a session.
    Only the session host can access this.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        try:
            session = TableSession.objects.select_related("table", "table__restaurant").get(
                id=session_id,
                status__in=["active", "payment_pending"],
            )
        except TableSession.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Session not found or already closed."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check if user is the host
        if session.host != request.user:
            # Check if user is a guest of this session
            if not session.guests.filter(user=request.user, is_host=True).exists():
                return Response(
                    {"success": False, "error": {"message": "Only the session host can get the invite code."}},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Generate invite URL (frontend will construct the actual link)
        invite_url = f"/join/{session.invite_code}"

        data = {
            "invite_code": session.invite_code,
            "invite_url": invite_url,
            "session_id": session.id,
            "table_number": session.table.number,
            "restaurant_name": session.table.restaurant.name,
        }

        return Response({"success": True, "data": SessionInviteResponseSerializer(data).data})


@extend_schema(tags=["Table Sessions"])
class JoinTableSessionPreviewView(APIView):
    """
    Get session info before joining (via invite code).
    """

    permission_classes = [AllowAny]

    def get(self, request, invite_code):
        try:
            session = TableSession.objects.select_related("table", "table__restaurant", "host").get(
                invite_code=invite_code,
                status__in=["active", "payment_pending"],
            )
        except TableSession.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Invalid invite code or session closed."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        data = {
            "session_id": session.id,
            "restaurant_name": session.table.restaurant.name,
            "restaurant_slug": session.table.restaurant.slug,
            "table_number": session.table.number,
            "table_name": session.table.name or "",
            "host_name": session.host.email if session.host else None,
            "guest_count": session.guests.filter(status="active").count(),
            "status": session.status,
        }

        return Response({"success": True, "data": SessionJoinPreviewSerializer(data).data})


@extend_schema(tags=["Table Sessions"])
class JoinTableSessionView(APIView):
    """
    Join a table session via invite code.
    Can be authenticated or anonymous (with guest_name).
    """

    permission_classes = [AllowAny]

    def post(self, request, invite_code):
        try:
            session = TableSession.objects.select_related("table", "table__restaurant").get(
                invite_code=invite_code,
                status__in=["active", "payment_pending"],
            )
        except TableSession.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Invalid invite code or session closed."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = JoinSessionSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        user = request.user if request.user.is_authenticated else None
        guest_name = serializer.validated_data.get("guest_name", "")

        # Check if user already in session
        if user:
            existing_guest = session.guests.filter(user=user, status="active").first()
            if existing_guest:
                return Response(
                    {
                        "success": True,
                        "message": "Already joined this session.",
                        "data": {
                            "guest": TableSessionGuestSerializer(existing_guest).data,
                            "session": TableSessionDetailSerializer(session).data,
                        },
                    }
                )

        # Create guest record
        guest, created = session.get_or_create_guest(user=user, guest_name=guest_name)

        return Response(
            {
                "success": True,
                "message": "Joined session successfully." if created else "Rejoined session.",
                "data": {
                    "guest": TableSessionGuestSerializer(guest).data,
                    "session": TableSessionDetailSerializer(session).data,
                },
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(tags=["Table Sessions"])
class TableSessionGuestsView(generics.ListAPIView):
    """List all guests at a table session."""

    serializer_class = TableSessionGuestSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        session_id = self.kwargs.get("session_id")
        return (
            TableSessionGuest.objects.filter(
                session_id=session_id,
                session__status__in=["active", "payment_pending"],
            )
            .select_related("user")
            .order_by("-is_host", "joined_at")
        )


@extend_schema(tags=["Table Sessions"])
class TableSessionOrdersView(APIView):
    """List all orders in a session."""

    permission_classes = [AllowAny]

    def get(self, request, session_id):
        from apps.orders.models import Order
        from apps.orders.serializers import OrderListSerializer

        try:
            session = TableSession.objects.get(id=session_id)
        except TableSession.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Session not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        orders = (
            Order.objects.filter(table_session=session)
            .select_related("customer", "session_guest")
            .order_by("-created_at")
        )

        serializer = OrderListSerializer(orders, many=True)

        return Response(
            {
                "success": True,
                "data": {
                    "session_id": str(session.id),
                    "orders": serializer.data,
                    "total_orders": orders.count(),
                },
            }
        )


@extend_schema(tags=["Table Sessions"])
class LeaveTableSessionView(APIView):
    """Leave a table session."""

    permission_classes = [AllowAny]

    def post(self, request, session_id):
        try:
            session = TableSession.objects.get(
                id=session_id,
                status__in=["active", "payment_pending"],
            )
        except TableSession.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Session not found or already closed."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        user = request.user if request.user.is_authenticated else None

        if user:
            guest = session.guests.filter(user=user, status="active").first()
        else:
            # For anonymous users, they need to provide guest_id
            guest_id = request.data.get("guest_id")
            if not guest_id:
                return Response(
                    {"success": False, "error": {"message": "guest_id is required for anonymous users."}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            guest = session.guests.filter(id=guest_id, user__isnull=True, status="active").first()

        if not guest:
            return Response(
                {"success": False, "error": {"message": "You are not a guest of this session."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cannot leave if host
        if guest.is_host:
            return Response(
                {"success": False, "error": {"message": "Host cannot leave the session. Close the session instead."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        guest.leave()

        return Response(
            {
                "success": True,
                "message": "Left session successfully.",
            }
        )
