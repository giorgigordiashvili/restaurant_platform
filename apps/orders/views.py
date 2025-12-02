"""
Views for orders app.
"""

from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import IsTenantManager
from apps.tables.models import Table, TableSession

from .models import Order, OrderItem, OrderItemModifier, OrderStatusHistory
from .serializers import (
    KitchenOrderSerializer,
    OrderCreateSerializer,
    OrderItemCreateSerializer,
    OrderItemSerializer,
    OrderListSerializer,
    OrderSerializer,
    OrderStatusHistorySerializer,
    OrderStatusUpdateSerializer,
)

# ============== Dashboard Views ==============


@extend_schema(tags=["Dashboard - Orders"])
class OrderListView(generics.ListAPIView):
    """List orders for a restaurant."""

    serializer_class = OrderListSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def get_queryset(self):
        queryset = (
            Order.objects.filter(restaurant=self.request.restaurant)
            .select_related("table")
            .prefetch_related("items")
            .order_by("-created_at")
        )

        # Filter by status
        order_status = self.request.query_params.get("status")
        if order_status:
            queryset = queryset.filter(status=order_status)

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
    permission_classes = [IsAuthenticated, IsTenantManager]
    lookup_field = "id"

    @require_restaurant
    def get_queryset(self):
        return (
            Order.objects.filter(restaurant=self.request.restaurant)
            .select_related("table")
            .prefetch_related("items__modifiers")
        )


@extend_schema(tags=["Dashboard - Orders"])
class OrderCreateView(APIView):
    """Create a new order."""

    permission_classes = [IsAuthenticated, IsTenantManager]

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

        # Create order
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
            handled_by=request.user,
        )

        # Add items
        for item_data in data["items"]:
            menu_item = item_data["menu_item_id"]  # Already validated as MenuItem

            order_item = OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                item_name=menu_item.safe_translation_getter("name", default=f"Item {menu_item.pk}"),
                item_description=menu_item.safe_translation_getter("description", default=""),
                unit_price=menu_item.price,
                quantity=item_data.get("quantity", 1),
                total_price=menu_item.price * item_data.get("quantity", 1),
                preparation_station=menu_item.preparation_station,
                special_instructions=item_data.get("special_instructions", ""),
            )

            # Add modifiers
            for modifier in item_data.get("modifier_ids", []):
                OrderItemModifier.objects.create(
                    order_item=order_item,
                    modifier=modifier,
                    modifier_name=modifier.safe_translation_getter("name", default=f"Modifier {modifier.pk}"),
                    price_adjustment=modifier.price_adjustment,
                )

            # Recalculate item total with modifiers
            order_item.recalculate_total()

        # Calculate order totals
        order.calculate_totals()

        # Record status history
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

    permission_classes = [IsAuthenticated, IsTenantManager]

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
        old_status = order.status

        # Update status based on type
        if new_status == "confirmed":
            order.confirm(data.get("estimated_minutes"))
        elif new_status == "cancelled":
            order.cancel(data.get("cancellation_reason", ""))
        elif new_status == "completed":
            order.complete()
        else:
            order.status = new_status
            order.save(update_fields=["status", "updated_at"])

        # Record status history
        OrderStatusHistory.objects.create(
            order=order,
            from_status=old_status,
            to_status=new_status,
            changed_by=request.user,
            notes=data.get("notes", ""),
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

    permission_classes = [IsAuthenticated, IsTenantManager]

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
        menu_item = data["menu_item_id"]

        order_item = OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            item_name=menu_item.safe_translation_getter("name", default=f"Item {menu_item.pk}"),
            item_description=menu_item.safe_translation_getter("description", default=""),
            unit_price=menu_item.price,
            quantity=data.get("quantity", 1),
            total_price=menu_item.price * data.get("quantity", 1),
            preparation_station=menu_item.preparation_station,
            special_instructions=data.get("special_instructions", ""),
        )

        # Add modifiers
        for modifier in data.get("modifier_ids", []):
            OrderItemModifier.objects.create(
                order_item=order_item,
                modifier=modifier,
                modifier_name=modifier.safe_translation_getter("name", default=f"Modifier {modifier.pk}"),
                price_adjustment=modifier.price_adjustment,
            )

        # Recalculate
        order_item.recalculate_total()
        order.calculate_totals()

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

    permission_classes = [IsAuthenticated, IsTenantManager]

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
    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def get_queryset(self):
        return (
            Order.objects.filter(
                restaurant=self.request.restaurant,
                status__in=["confirmed", "preparing"],
            )
            .select_related("table")
            .prefetch_related("items__modifiers")
            .order_by("created_at")
        )


@extend_schema(tags=["Dashboard - Orders"])
class OrderHistoryView(generics.ListAPIView):
    """Get order status history."""

    serializer_class = OrderStatusHistorySerializer
    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def get_queryset(self):
        order_id = self.kwargs.get("order_id")
        return OrderStatusHistory.objects.filter(
            order_id=order_id,
            order__restaurant=self.request.restaurant,
        ).order_by("-created_at")


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

        serializer = OrderCreateSerializer(
            data=request.data,
            context={"restaurant": restaurant},
        )
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # Get table
        table = None
        session = None

        if data.get("table_id"):
            try:
                table = Table.objects.get(id=data["table_id"], restaurant=restaurant)
            except Table.DoesNotExist:
                return Response(
                    {"success": False, "error": {"message": "Table not found."}},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # Create order
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
        )

        # Add items
        for item_data in data["items"]:
            menu_item = item_data["menu_item_id"]

            order_item = OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                item_name=menu_item.safe_translation_getter("name", default=f"Item {menu_item.pk}"),
                item_description=menu_item.safe_translation_getter("description", default=""),
                unit_price=menu_item.price,
                quantity=item_data.get("quantity", 1),
                total_price=menu_item.price * item_data.get("quantity", 1),
                preparation_station=menu_item.preparation_station,
                special_instructions=item_data.get("special_instructions", ""),
            )

            # Add modifiers
            for modifier in item_data.get("modifier_ids", []):
                OrderItemModifier.objects.create(
                    order_item=order_item,
                    modifier=modifier,
                    modifier_name=modifier.safe_translation_getter("name", default=f"Modifier {modifier.pk}"),
                    price_adjustment=modifier.price_adjustment,
                )

            order_item.recalculate_total()

        order.calculate_totals()

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
    """Check order status (for customer)."""

    permission_classes = [AllowAny]

    def get(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Order not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "success": True,
                "data": {
                    "order_number": order.order_number,
                    "status": order.status,
                    "status_display": order.get_status_display(),
                    "estimated_ready_at": order.estimated_ready_at,
                    "total": str(order.total),
                },
            }
        )
