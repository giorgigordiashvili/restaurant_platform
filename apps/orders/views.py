"""
Views for orders app.
"""

from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import generics
from rest_framework import serializers as serializers_module
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired, staff_can
from apps.crm import hooks as crm_hooks
from apps.inventory import hooks as inventory_hooks
from apps.notifications import hooks as notification_hooks
from apps.promotions import hooks as promotion_hooks
from apps.promotions import services as promotion_services
from apps.tables.models import Table, TableSession

from . import services
from .models import Order, OrderDiscount, OrderItem, OrderItemModifier, OrderStatusHistory
from .serializers import (
    KitchenOrderSerializer,
    OrderCreateSerializer,
    OrderDiscountCreateSerializer,
    OrderDiscountDeleteSerializer,
    OrderDiscountSerializer,
    OrderItemCreateSerializer,
    OrderItemDiscountSerializer,
    OrderItemReasonSerializer,
    OrderItemSerializer,
    OrderListSerializer,
    OrderMoveSerializer,
    OrderSerializer,
    OrderSplitSerializer,
    OrderStatusHistorySerializer,
    OrderStatusUpdateSerializer,
)
from .services import transition_order


def _order_error(exc: services.OrderError, http_status=status.HTTP_409_CONFLICT):
    return Response({"success": False, "error": exc.as_dict()}, status=http_status)


def _reason(request, data, kind):
    """Resolve reason_id -> DiscountReason (scoped) and tell whether the caller may use manager-only reasons."""
    from apps.payments.models import DiscountReason

    reason = None
    if data.get("reason_id"):
        reason = get_object_or_404(DiscountReason, id=data["reason_id"], restaurant=request.restaurant, kind=kind)
    return reason, data.get("reason_text", ""), staff_can(request, "cash", "update")


# ============== Dashboard Views ==============


@extend_schema(tags=["Dashboard - Orders"])
class OrderListView(generics.ListAPIView):
    """List orders for a restaurant."""

    serializer_class = OrderListSerializer
    # Role-based: kitchen/bar/waiter get here through StaffRole permissions.
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("orders", "read")

    @require_restaurant
    def get_queryset(self):
        queryset = (
            Order.objects.filter(restaurant=self.request.restaurant)
            .select_related("table", "reservation")
            .prefetch_related("items")
            .order_by("-created_at")
        )

        # Kitchen should only see orders whose linked reservation has been
        # confirmed. Walk-in / QR-dine-in / takeaway orders have no
        # reservation and are always visible. Dashboard consumers can
        # opt-out with ?include_pending_reservations=true if they truly
        # want everything.
        include_pending = self.request.query_params.get("include_pending_reservations")
        if not (include_pending and include_pending.lower() == "true"):
            queryset = queryset.filter(
                Q(reservation__isnull=True) | Q(reservation__status__in=["confirmed", "seated", "completed"])
            )

        # Filter by status
        order_status = self.request.query_params.get("status")
        if order_status:
            queryset = queryset.filter(status=order_status)

        # Filter by source (web / qr / pos / glovo ...)
        source = self.request.query_params.get("source")
        if source:
            queryset = queryset.filter(source=source)

        # Filter by order type
        order_type = self.request.query_params.get("type")
        if order_type:
            queryset = queryset.filter(order_type=order_type)

        # Filter by table
        table_id = self.request.query_params.get("table")
        if table_id:
            queryset = queryset.filter(table_id=table_id)

        # Filter by date
        date = self.request.query_params.get("date")
        if date:
            queryset = queryset.filter(created_at__date=date)

        # Active orders only (not completed/cancelled)
        active_only = self.request.query_params.get("active")
        if active_only and active_only.lower() == "true":
            queryset = queryset.exclude(status__in=["completed", "cancelled"])

        return queryset


@extend_schema(tags=["Dashboard - Orders"])
class OrderDetailView(generics.RetrieveAPIView):
    """Get order details."""

    serializer_class = OrderSerializer
    # Role-based: kitchen/bar/waiter get here through StaffRole permissions.
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("orders", "read")
    lookup_field = "id"

    @require_restaurant
    def get_queryset(self):
        return (
            Order.objects.filter(restaurant=self.request.restaurant)
            .select_related("table")
            .prefetch_related("items__modifiers")
        )


def _add_items(order, items_data):
    """Create OrderItem rows (+ modifiers) from validated item payloads; returns them."""
    created = []
    for item_data in items_data:
        menu_item = item_data["menu_item_id"]  # already validated as a MenuItem
        quantity = item_data.get("quantity", 1)
        order_item = OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            item_name=menu_item.safe_translation_getter("name", default=f"Item {menu_item.pk}"),
            item_description=menu_item.safe_translation_getter("description", default=""),
            unit_price=menu_item.price,
            quantity=quantity,
            total_price=menu_item.price * quantity,
            preparation_station=menu_item.preparation_station,
            special_instructions=item_data.get("special_instructions", ""),
        )
        for modifier in item_data.get("modifier_ids", []):
            OrderItemModifier.objects.create(
                order_item=order_item,
                modifier=modifier,
                modifier_name=modifier.safe_translation_getter("name", default=f"Modifier {modifier.pk}"),
                price_adjustment=modifier.price_adjustment,
            )
        order_item.recalculate_total()
        created.append(order_item)
    return created


@extend_schema(tags=["Dashboard - Orders"])
class OrderCreateView(APIView):
    """Create a new order."""

    # Role-based: kitchen/bar/waiter get here through StaffRole permissions.
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("orders", "create")

    @require_restaurant
    def post(self, request):
        serializer = OrderCreateSerializer(
            data=request.data,
            context={"restaurant": request.restaurant},
        )
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # Get table and session if provided
        table = None
        session = None

        if data.get("session_id"):
            try:
                session = TableSession.objects.get(
                    id=data["session_id"],
                    table__restaurant=request.restaurant,
                    status="active",
                )
                table = session.table
            except TableSession.DoesNotExist:
                return Response(
                    {"success": False, "error": {"message": "Active session not found."}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif data.get("table_id"):
            try:
                table = Table.objects.get(
                    id=data["table_id"],
                    restaurant=request.restaurant,
                )
            except Table.DoesNotExist:
                return Response(
                    {"success": False, "error": {"message": "Table not found."}},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # One transaction: a stock shortage (InsufficientStock -> 409) or any
        # other failure part-way through must not leave a half-built order.
        with transaction.atomic():
            order = Order.objects.create(
                restaurant=request.restaurant,
                table=table,
                table_session=session,
                customer=request.user if request.user.is_authenticated else None,
                order_type=data.get("order_type", "dine_in"),
                customer_name=data.get("customer_name", ""),
                customer_phone=data.get("customer_phone", ""),
                customer_email=data.get("customer_email", ""),
                customer_notes=data.get("customer_notes", ""),
                delivery_address=data.get("delivery_address", ""),
                tip_amount=data.get("tip_amount", 0),
                handled_by=request.user,
                source="pos",
            )
            _add_items(order, data["items"])
            order.calculate_totals()
            promotion_hooks.on_order_items_changed(order, channel="pos")
            if data.get("promo_code"):
                try:
                    promotion_services.redeem_code(order, data["promo_code"], by=request.user, channel="pos")
                except promotion_services.PromotionError as exc:
                    raise serializers_module.ValidationError({"promo_code": exc.message})
            inventory_hooks.on_order_created(order)
            notification_hooks.on_order_created(order, by=request.user)
            crm_hooks.on_order_created(order, consent=bool(data.get("marketing_opt_in")))

            OrderStatusHistory.objects.create(
                order=order,
                from_status="",
                to_status="pending",
                changed_by=request.user,
                notes="Order created",
            )

        return Response(
            {
                "success": True,
                "message": "Order created.",
                "data": OrderSerializer(order).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Dashboard - Orders"])
class OrderStatusUpdateView(APIView):
    """Update order status."""

    # Role-based: kitchen/bar/waiter get here through StaffRole permissions.
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("orders", "update")

    @require_restaurant
    def patch(self, request, id):
        try:
            order = Order.objects.get(id=id, restaurant=request.restaurant)
        except Order.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Order not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = OrderStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        new_status = data["status"]

        transition_order(
            order,
            new_status,
            by=request.user,
            notes=data.get("notes", ""),
            estimated_minutes=data.get("estimated_minutes"),
            cancellation_reason=data.get("cancellation_reason", ""),
        )

        return Response(
            {
                "success": True,
                "message": f"Order status updated to {new_status}.",
                "data": OrderSerializer(order).data,
            }
        )


@extend_schema(tags=["Dashboard - Orders"])
class OrderAddItemView(APIView):
    """Add item to an existing order."""

    # Role-based: kitchen/bar/waiter get here through StaffRole permissions.
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request, id):
        try:
            order = Order.objects.get(id=id, restaurant=request.restaurant)
        except Order.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Order not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not order.is_editable:
            return Response(
                {"success": False, "error": {"message": "Order can no longer be modified."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = OrderItemCreateSerializer(
            data=request.data,
            context={"restaurant": request.restaurant},
        )
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        with transaction.atomic():
            order_item = _add_items(order, [data])[0]
            order.calculate_totals()
            promotion_hooks.on_order_items_changed(order, channel="pos")
            inventory_hooks.on_order_items_added(order, [order_item])
        from apps.printing import hooks as printing_hooks

        printing_hooks.on_items_added(order, [order_item], by=request.user)

        return Response(
            {
                "success": True,
                "message": "Item added to order.",
                "data": OrderSerializer(order).data,
            }
        )


@extend_schema(tags=["Dashboard - Orders"])
class OrderItemStatusUpdateView(APIView):
    """Update order item status (for kitchen)."""

    # Role-based: kitchen/bar/waiter get here through StaffRole permissions.
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("orders", "update")

    @require_restaurant
    def patch(self, request, order_id, item_id):
        try:
            item = OrderItem.objects.get(
                id=item_id,
                order_id=order_id,
                order__restaurant=request.restaurant,
            )
        except OrderItem.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Order item not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        new_status = request.data.get("status")
        if new_status not in dict(OrderItem.STATUS_CHOICES):
            return Response(
                {"success": False, "error": {"message": "Invalid status."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_status == "cancelled":
            # Cancelling a line is a void: who / why / when, stock back, totals redone.
            reason, reason_text, manager = _reason(request, request.data, "void")
            try:
                services.void_item(item, by=request.user, reason=reason, reason_text=reason_text, manager=manager)
            except services.OrderError as exc:
                return _order_error(exc, status.HTTP_400_BAD_REQUEST)
        else:
            if item.status == "cancelled":
                return Response(
                    {"success": False, "error": {"code": "item_voided", "message": "This item was voided."}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            item.status = new_status
            item.save(update_fields=["status", "updated_at"])

        return Response(
            {
                "success": True,
                "message": f"Item status updated to {new_status}.",
                "data": OrderItemSerializer(item).data,
            }
        )


@extend_schema(tags=["Dashboard - Orders"])
class KitchenOrdersView(generics.ListAPIView):
    """Get orders for kitchen display."""

    serializer_class = KitchenOrderSerializer
    # Role-based: kitchen/bar/waiter get here through StaffRole permissions.
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("orders", "read")

    KITCHEN_STATUSES = ("confirmed", "preparing", "ready")

    @require_restaurant
    def get_queryset(self):
        statuses = [s for s in self.request.query_params.get("status", "").split(",") if s in self.KITCHEN_STATUSES]
        return (
            Order.objects.filter(
                restaurant=self.request.restaurant,
                status__in=statuses or self.KITCHEN_STATUSES,
            )
            .select_related("table")
            .prefetch_related("items__modifiers")
            .order_by(F("confirmed_at").asc(nulls_last=True), "created_at")
        )


@extend_schema(tags=["Dashboard - Orders"])
class OrderHistoryView(generics.ListAPIView):
    """Get order status history."""

    serializer_class = OrderStatusHistorySerializer
    # Role-based: kitchen/bar/waiter get here through StaffRole permissions.
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("orders", "read")

    @require_restaurant
    def get_queryset(self):
        order_id = self.kwargs.get("order_id")
        return OrderStatusHistory.objects.filter(
            order_id=order_id,
            order__restaurant=self.request.restaurant,
        ).order_by("-created_at")


# ============== Discounts / comps / voids / split / move ==============


class _OrderMoneyView(APIView):
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("cash")]

    def _order(self, request, id):
        return get_object_or_404(Order, id=id, restaurant=request.restaurant)

    def _item(self, request, order_id, item_id):
        return get_object_or_404(OrderItem, id=item_id, order_id=order_id, order__restaurant=request.restaurant)


@extend_schema(tags=["Dashboard - Orders"], request=OrderDiscountCreateSerializer, responses={200: OrderSerializer})
class OrderDiscountView(_OrderMoneyView):
    """POST adds an order-level discount (percent or fixed); DELETE removes one (or all manual ones)."""

    required_permission = ("cash", "update")

    @require_restaurant
    def post(self, request, id):
        order = self._order(request, id)
        serializer = OrderDiscountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        reason, reason_text, manager = _reason(request, data, "discount")
        try:
            services.apply_discount(
                order,
                mode=data["mode"],
                value=data["value"],
                by=request.user,
                reason=reason,
                reason_text=reason_text,
                manager=manager,
            )
        except services.OrderError as exc:
            return _order_error(exc)
        order.refresh_from_db()
        return Response({"success": True, "message": "Discount applied.", "data": OrderSerializer(order).data})

    @require_restaurant
    def delete(self, request, id):
        order = self._order(request, id)
        serializer = OrderDiscountDeleteSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        discount_id = serializer.validated_data.get("discount_id")
        qs = order.discounts.all()
        qs = qs.filter(id=discount_id) if discount_id else qs.filter(kind="manual")
        try:
            for d in list(qs):
                services.remove_discount(order, d, by=request.user)
        except services.OrderError as exc:
            return _order_error(exc)
        order.refresh_from_db()
        return Response({"success": True, "message": "Discount removed.", "data": OrderSerializer(order).data})


@extend_schema(tags=["Dashboard - Orders"], request=OrderItemDiscountSerializer, responses={200: OrderSerializer})
class OrderItemDiscountView(_OrderMoneyView):
    required_permission = ("cash", "update")

    @require_restaurant
    def post(self, request, order_id, item_id):
        item = self._item(request, order_id, item_id)
        serializer = OrderItemDiscountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        reason, reason_text, manager = _reason(request, data, "discount")
        try:
            services.discount_item(
                item,
                mode=data["mode"],
                value=data["value"],
                by=request.user,
                reason=reason,
                reason_text=reason_text,
                manager=manager,
            )
        except services.OrderError as exc:
            return _order_error(exc)
        item.order.refresh_from_db()
        return Response({"success": True, "message": "Item discounted.", "data": OrderSerializer(item.order).data})

    @require_restaurant
    def delete(self, request, order_id, item_id):
        item = self._item(request, order_id, item_id)
        try:
            services.clear_item_discount(item, by=request.user)
        except services.OrderError as exc:
            return _order_error(exc)
        item.order.refresh_from_db()
        return Response(
            {"success": True, "message": "Item discount removed.", "data": OrderSerializer(item.order).data}
        )


@extend_schema(tags=["Dashboard - Orders"], request=OrderItemReasonSerializer, responses={200: OrderSerializer})
class OrderItemCompView(_OrderMoneyView):
    required_permission = ("cash", "update")

    @require_restaurant
    def post(self, request, order_id, item_id):
        item = self._item(request, order_id, item_id)
        serializer = OrderItemReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason, reason_text, manager = _reason(request, serializer.validated_data, "comp")
        try:
            services.comp_item(item, by=request.user, reason=reason, reason_text=reason_text, manager=manager)
        except services.OrderError as exc:
            return _order_error(exc)
        item.order.refresh_from_db()
        return Response({"success": True, "message": "Item comped.", "data": OrderSerializer(item.order).data})


@extend_schema(tags=["Dashboard - Orders"], request=OrderItemReasonSerializer, responses={200: OrderSerializer})
class OrderItemVoidView(APIView):
    """Void a line. Any role that may update orders; manager-only reasons need 'Cash & payments: edit'."""

    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request, order_id, item_id):
        item = get_object_or_404(OrderItem, id=item_id, order_id=order_id, order__restaurant=request.restaurant)
        serializer = OrderItemReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason, reason_text, manager = _reason(request, serializer.validated_data, "void")
        try:
            services.void_item(item, by=request.user, reason=reason, reason_text=reason_text, manager=manager)
        except services.OrderError as exc:
            return _order_error(exc)
        item.order.refresh_from_db()
        return Response({"success": True, "message": "Item voided.", "data": OrderSerializer(item.order).data})


@extend_schema(tags=["Dashboard - Orders"], request=OrderSplitSerializer, responses={201: OrderSerializer})
class OrderSplitView(_OrderMoneyView):
    """Move some items to a new order (same table unless table_id / session_id given) so they can be paid separately."""

    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request, id):
        order = self._order(request, id)
        serializer = OrderSplitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        table = session = None
        if data.get("table_id"):
            table = get_object_or_404(Table, id=data["table_id"], restaurant=request.restaurant)
        if data.get("session_id"):
            session = get_object_or_404(TableSession, id=data["session_id"], table__restaurant=request.restaurant)
        try:
            new_order = services.split_items(order, data["item_ids"], by=request.user, table=table, session=session)
        except services.OrderError as exc:
            return _order_error(exc)
        order.refresh_from_db()
        return Response(
            {
                "success": True,
                "message": f"Split to {new_order.order_number}.",
                "data": {"order": OrderSerializer(order).data, "new_order": OrderSerializer(new_order).data},
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Dashboard - Orders"], request=OrderMoveSerializer, responses={200: OrderSerializer})
class OrderMoveView(APIView):
    """Re-seat an order at another table."""

    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request, id):
        order = get_object_or_404(Order, id=id, restaurant=request.restaurant)
        serializer = OrderMoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        table = get_object_or_404(Table, id=serializer.validated_data["table_id"], restaurant=request.restaurant)
        try:
            services.move_order(order, table, by=request.user)
        except services.OrderError as exc:
            return _order_error(exc)
        order.refresh_from_db()
        return Response(
            {"success": True, "message": f"Moved to table {table.number}.", "data": OrderSerializer(order).data}
        )


# ============== Tip distribution (Phase-3) =====================


@extend_schema(tags=["Dashboard - Orders"])
class OrderServerAssignView(APIView):
    """
    Assign a staff member as the server for an order, so the tip
    amount gets credited to them in the tip-distribution report.

    PATCH body: {"server_id": "<user-uuid>" | null}
    """

    # Role-based: kitchen/bar/waiter get here through StaffRole permissions.
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("orders", "update")

    @require_restaurant
    def patch(self, request, id):
        try:
            order = Order.objects.get(id=id, restaurant=request.restaurant)
        except Order.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Order not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        server_id = request.data.get("server_id")
        if server_id is None:
            order.server = None
        else:
            from apps.accounts.models import User
            from apps.staff.models import StaffMember

            try:
                user = User.objects.get(id=server_id)
            except User.DoesNotExist:
                return Response(
                    {"success": False, "error": {"message": "User not found."}},
                    status=status.HTTP_404_NOT_FOUND,
                )
            # Guard: user must be an active staff member of this restaurant.
            if not StaffMember.objects.filter(user=user, restaurant=request.restaurant, is_active=True).exists():
                return Response(
                    {
                        "success": False,
                        "error": {
                            "message": "User isn't a staff member of this restaurant.",
                        },
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            order.server = user

        # Default full-credit tip allocation to the assigned server.
        if order.server and order.tip_amount:
            order.tip_distribution = {str(order.server.id): str(order.tip_amount)}
        order.save(update_fields=["server", "tip_distribution", "updated_at"])

        return Response({"success": True, "data": OrderSerializer(order).data})


@extend_schema(tags=["Dashboard - Orders"])
class TipReportView(APIView):
    """
    GET /dashboard/orders/tips/?from=YYYY-MM-DD&to=YYYY-MM-DD

    Per-server tip totals for a date range. Aggregates Order.tip_amount
    across orders whose status is anything but cancelled; tips with no
    assigned server roll up under the "unassigned" bucket for the
    restaurant to distribute manually.
    """

    # Role-based: kitchen/bar/waiter get here through StaffRole permissions.
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("analytics", "read")

    @require_restaurant
    def get(self, request):
        from datetime import date
        from decimal import Decimal

        today = timezone.localdate() if hasattr(timezone, "localdate") else date.today()
        raw_from = request.query_params.get("from") or today.isoformat()
        raw_to = request.query_params.get("to") or today.isoformat()

        qs = (
            Order.objects.filter(restaurant=request.restaurant, tip_amount__gt=0)
            .exclude(status="cancelled")
            .filter(created_at__date__gte=raw_from, created_at__date__lte=raw_to)
            .select_related("server")
        )
        by_server: dict = {}
        for o in qs:
            key = str(o.server_id) if o.server_id else "unassigned"
            bucket = by_server.setdefault(
                key,
                {
                    "server_id": str(o.server_id) if o.server_id else None,
                    "server_email": o.server.email if o.server_id else None,
                    "server_name": o.server.get_full_name() if o.server_id else None,
                    "total_tips": Decimal("0"),
                    "order_count": 0,
                },
            )
            bucket["total_tips"] += o.tip_amount or Decimal("0")
            bucket["order_count"] += 1

        # stringify decimals for JSON
        for b in by_server.values():
            b["total_tips"] = str(b["total_tips"])

        grand = sum((o.tip_amount or Decimal("0")) for o in qs)
        return Response(
            {
                "success": True,
                "data": {
                    "from": raw_from,
                    "to": raw_to,
                    "grand_total": str(grand),
                    "order_count": qs.count(),
                    "by_server": list(by_server.values()),
                },
            }
        )


# ============== Public Views (for customer ordering) ==============


@extend_schema(tags=["Orders"])
class CustomerOrderCreateView(APIView):
    """Create order from customer (via QR code scan)."""

    permission_classes = [AllowAny]

    def post(self, request):
        # Get restaurant from slug in request
        restaurant_slug = request.data.get("restaurant_slug")
        if not restaurant_slug:
            return Response(
                {"success": False, "error": {"message": "Restaurant slug is required."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.tenants.models import Restaurant

        try:
            restaurant = Restaurant.objects.get(slug=restaurant_slug, is_active=True)
        except Restaurant.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Restaurant not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Menu-only mode — restaurant has flipped its master ordering switch
        # off. The customer site should already hide every order surface, so
        # this is mostly a belt-and-braces guard against direct API clients.
        if not restaurant.accepts_remote_orders:
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "ordering_disabled",
                        "message": "Ordering is disabled at this restaurant.",
                    },
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = OrderCreateSerializer(
            data=request.data,
            context={"restaurant": restaurant},
        )
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        if data.get("order_type", "dine_in") != "dine_in" and not restaurant.accepts_takeaway:
            return Response(
                {
                    "success": False,
                    "error": {"code": "takeaway_disabled", "message": "This restaurant only takes dine-in orders."},
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Get table
        table = None
        session = None

        if data.get("session_id"):
            try:
                session = TableSession.objects.get(
                    id=data["session_id"],
                    table__restaurant=restaurant,
                )
            except TableSession.DoesNotExist:
                return Response(
                    {
                        "success": False,
                        "error": {
                            "code": "session_not_found",
                            "message": "Table session not found.",
                        },
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )
            if session.status != "active":
                return Response(
                    {
                        "success": False,
                        "error": {
                            "code": "session_closed",
                            "message": "This table session has ended. Scan a new QR to start over.",
                        },
                    },
                    status=status.HTTP_410_GONE,
                )
            table = session.table

        if data.get("table_id") and table is None:
            try:
                table = Table.objects.get(id=data["table_id"], restaurant=restaurant)
            except Table.DoesNotExist:
                return Response(
                    {"success": False, "error": {"message": "Table not found."}},
                    status=status.HTTP_404_NOT_FOUND,
                )

        with transaction.atomic():
            order = Order.objects.create(
                restaurant=restaurant,
                table=table,
                table_session=session,
                customer=request.user if request.user.is_authenticated else None,
                order_type=data.get("order_type", "dine_in"),
                customer_name=data.get("customer_name", ""),
                customer_phone=data.get("customer_phone", ""),
                customer_email=data.get("customer_email", ""),
                customer_notes=data.get("customer_notes", ""),
                delivery_address=data.get("delivery_address", ""),
                tip_amount=data.get("tip_amount", 0),
                source="qr" if session is not None else "web",
            )
            _add_items(order, data["items"])
            order.calculate_totals()
            promotion_hooks.on_order_items_changed(order, channel=order.source)
            if data.get("promo_code"):
                try:
                    promotion_services.redeem_code(order, data["promo_code"], by=request.user, channel=order.source)
                except promotion_services.PromotionError as exc:
                    raise serializers_module.ValidationError({"promo_code": exc.message})
            # Reserves ingredients; raises InsufficientStock (409) and rolls
            # the order back when the warehouse cannot cover it.
            inventory_hooks.on_order_created(order)
            notification_hooks.on_order_created(order, by=request.user)
            crm_hooks.on_order_created(order, consent=bool(data.get("marketing_opt_in")))

            OrderStatusHistory.objects.create(
                order=order,
                from_status="",
                to_status="pending",
                notes="Order created by customer",
            )

        return Response(
            {
                "success": True,
                "message": "Order placed successfully.",
                "data": {
                    "order_number": order.order_number,
                    "total": str(order.total),
                },
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Orders"])
class CustomerOrderStatusView(APIView):
    """
    Customer-facing order detail.

    Open to anonymous traffic — the order_number is the lookup key, and BOG
    guarantees it's a wide-enough namespace that guessing isn't practical at
    our scale. For a richer, auth-gated variant see ``CustomerMyOrdersListView``
    below.
    """

    permission_classes = [AllowAny]

    def get(self, request, order_number):
        try:
            order = (
                Order.objects.select_related("restaurant", "table")
                .prefetch_related("items__modifiers")
                .get(order_number=order_number)
            )
        except Order.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Order not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(OrderSerializer(order).data)


@extend_schema(tags=["Orders"])
class CustomerMyOrdersListView(generics.ListAPIView):
    """List orders belonging to the authenticated user, newest first."""

    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = (
            Order.objects.filter(customer=self.request.user)
            .select_related("restaurant", "table")
            .prefetch_related("items__modifiers")
            .order_by("-created_at")
        )
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs


@extend_schema(tags=["Orders"])
class CustomerMyOrderDetailView(generics.RetrieveAPIView):
    """Retrieve one of the authenticated user's orders by order_number."""

    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "order_number"

    def get_queryset(self):
        return (
            Order.objects.filter(customer=self.request.user)
            .select_related("restaurant", "table")
            .prefetch_related("items__modifiers")
        )
