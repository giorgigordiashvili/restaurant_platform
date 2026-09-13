"""
Public: ordering config / slots / delivery quote for the restaurant page.
Dashboard: settings, zones, couriers, deliveries and courier actions, domains.
"""

from __future__ import annotations

from datetime import date

from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiParameter, extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired, staff_can
from apps.ordering import dispatch, domains, services
from apps.ordering.models import Courier, Delivery, DeliveryZone, RestaurantDomain
from apps.ordering.serializers import (
    AssignCourierSerializer,
    CancelCourierSerializer,
    CourierSerializer,
    CourierUpdateSerializer,
    DeliveryQuoteRequestSerializer,
    DeliverySerializer,
    DeliveryZoneSerializer,
    OnlineOrderingSettingsSerializer,
    PauseSerializer,
    RequestCourierSerializer,
    RestaurantDomainSerializer,
    SummarySerializer,
)
from apps.orders.models import Order
from apps.tenants.models import Restaurant

TAG = "Dashboard - Online ordering"
PUBLIC_TAG = "Ordering"
PERMS = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("online_ordering")]


def _err(code: str, message: str, http=status.HTTP_400_BAD_REQUEST, **extra):
    return Response({"success": False, "error": {"code": code, "message": message, **extra}}, status=http)


def _public_restaurant(slug: str) -> Restaurant:
    return get_object_or_404(Restaurant, slug=slug, is_active=True)


class QuoteThrottle(AnonRateThrottle):
    scope = "ordering_quote"
    rate = "60/min"


# ── public ─────────────────────────────────────────────────────────────────


@extend_schema(tags=[PUBLIC_TAG])
class PublicConfigView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        return Response({"success": True, "data": services.public_config(_public_restaurant(slug))})


@extend_schema(
    tags=[PUBLIC_TAG],
    parameters=[
        OpenApiParameter("kind", str, description="takeaway | delivery"),
        OpenApiParameter("date", str, description="YYYY-MM-DD (restaurant timezone)"),
    ],
)
class PublicSlotsView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, slug):
        restaurant = _public_restaurant(slug)
        kind = request.query_params.get("kind", "takeaway")
        if kind not in ("takeaway", "delivery"):
            return _err("bad_kind", "kind must be takeaway or delivery.")
        raw = request.query_params.get("date")
        try:
            day = date.fromisoformat(raw) if raw else services.H.local_now(restaurant).date()
        except ValueError:
            return _err("bad_date", "date must be YYYY-MM-DD.")
        return Response(
            {"success": True, "data": {"date": day.isoformat(), "slots": services.slots(restaurant, kind, day)}}
        )


@extend_schema(tags=[PUBLIC_TAG], request=DeliveryQuoteRequestSerializer)
class PublicDeliveryQuoteView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [QuoteThrottle]

    def post(self, request, slug):
        restaurant = _public_restaurant(slug)
        s = DeliveryQuoteRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            q = services.quote_delivery(
                restaurant, s.validated_data["lat"], s.validated_data["lng"], s.validated_data["subtotal"]
            )
        except services.FulfilmentError as exc:
            return _err(exc.code, exc.message, **exc.extra)
        return Response({"success": True, "data": q.as_dict()})


@extend_schema(tags=[PUBLIC_TAG], parameters=[OpenApiParameter("host", str)])
class RestaurantByDomainView(APIView):
    """The customer site's middleware asks which restaurant owns a custom domain (cached by the edge)."""

    permission_classes = [AllowAny]

    def get(self, request):
        row = domains.lookup(request.query_params.get("host", ""))
        if row is None:
            return Response({"success": False, "error": {"code": "unknown_domain"}}, status=status.HTTP_404_NOT_FOUND)
        r = row.restaurant
        return Response(
            {
                "success": True,
                "data": {"slug": r.slug, "name": r.name, "locale": r.default_language, "verified": row.is_verified},
            }
        )


@extend_schema(exclude=True)
class DomainCheckView(APIView):
    """Caddy on-demand TLS ``ask`` endpoint: 200 only for domains a restaurant registered."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request):
        host = request.query_params.get("domain", "")
        if domains.is_known(host):
            return Response({"ok": True})
        return Response({"ok": False}, status=status.HTTP_404_NOT_FOUND)


# ── dashboard ──────────────────────────────────────────────────────────────


@extend_schema(tags=[TAG], responses=SummarySerializer)
class SummaryView(APIView):
    permission_classes = PERMS
    required_permission = ("orders", "read")

    @require_restaurant
    def get(self, request):
        return Response(SummarySerializer(services.summary(request.restaurant)).data)


@extend_schema(tags=[TAG], request=OnlineOrderingSettingsSerializer, responses=OnlineOrderingSettingsSerializer)
class SettingsView(APIView):
    permission_classes = PERMS
    required_permission = ("settings", "read")

    @require_restaurant
    def get(self, request):
        return Response(OnlineOrderingSettingsSerializer(services.settings_for(request.restaurant)).data)

    @require_restaurant
    def patch(self, request):
        if not staff_can(request, "settings", "update"):
            return _err("forbidden", "settings:update needed.", status.HTTP_403_FORBIDDEN)
        cfg = services.settings_for(request.restaurant)
        s = OnlineOrderingSettingsSerializer(cfg, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data)


@extend_schema(tags=[TAG], request=PauseSerializer, responses=OnlineOrderingSettingsSerializer)
class PauseView(APIView):
    permission_classes = PERMS
    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request):
        s = PauseSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        cfg = services.pause(
            request.restaurant, s.validated_data["minutes"], s.validated_data["reason"], by=request.user
        )
        return Response(OnlineOrderingSettingsSerializer(cfg).data)

    @require_restaurant
    def delete(self, request):
        cfg = services.resume(request.restaurant, by=request.user)
        return Response(OnlineOrderingSettingsSerializer(cfg).data)


@extend_schema(tags=[TAG])
class ZoneListView(generics.ListCreateAPIView):
    permission_classes = PERMS
    required_permission = ("settings", "read")
    serializer_class = DeliveryZoneSerializer
    pagination_class = None

    @require_restaurant
    def get_queryset(self):
        return DeliveryZone.objects.filter(restaurant=self.request.restaurant)

    def perform_create(self, serializer):
        if not staff_can(self.request, "settings", "update"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("settings:update needed.")
        serializer.save(restaurant=self.request.restaurant)


@extend_schema(tags=[TAG])
class ZoneDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = PERMS
    required_permission = ("settings", "update")
    serializer_class = DeliveryZoneSerializer
    lookup_url_kwarg = "zone_id"

    @require_restaurant
    def get_queryset(self):
        return DeliveryZone.objects.filter(restaurant=self.request.restaurant)


@extend_schema(tags=[TAG])
class CourierListView(generics.ListCreateAPIView):
    permission_classes = PERMS
    required_permission = ("orders", "read")
    serializer_class = CourierSerializer
    pagination_class = None

    @require_restaurant
    def get_queryset(self):
        qs = Courier.objects.filter(restaurant=self.request.restaurant).select_related("staff")
        if self.request.query_params.get("active") in ("1", "true"):
            qs = qs.filter(is_active=True)
        return qs

    def perform_create(self, serializer):
        if not staff_can(self.request, "staff", "update"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("staff:update needed.")
        serializer.save(restaurant=self.request.restaurant)


@extend_schema(tags=[TAG])
class CourierDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = PERMS
    required_permission = ("staff", "update")
    serializer_class = CourierSerializer
    lookup_url_kwarg = "courier_id"

    @require_restaurant
    def get_queryset(self):
        return Courier.objects.filter(restaurant=self.request.restaurant)


@extend_schema(tags=[TAG], responses=CourierSerializer)
class MyCourierView(APIView):
    """The signed-in rider's own courier row (POS courier mode); PATCH toggles availability."""

    permission_classes = PERMS
    required_permission = ("orders", "read")

    def _row(self, request):
        return (
            Courier.objects.filter(restaurant=request.restaurant, staff__user=request.user, is_active=True)
            .select_related("staff")
            .first()
        )

    @require_restaurant
    def get(self, request):
        row = self._row(request)
        if row is None:
            return Response({"detail": "not a courier"}, status=status.HTTP_404_NOT_FOUND)
        return Response(CourierSerializer(row).data)

    @require_restaurant
    def patch(self, request):
        row = self._row(request)
        if row is None:
            return Response({"detail": "not a courier"}, status=status.HTTP_404_NOT_FOUND)
        if "is_available" in request.data:
            row.is_available = bool(request.data["is_available"])
            row.save(update_fields=["is_available", "updated_at"])
        return Response(CourierSerializer(row).data)


@extend_schema(tags=[TAG], parameters=[OpenApiParameter("status", str), OpenApiParameter("mine", bool)])
class DeliveryListView(generics.ListAPIView):
    permission_classes = PERMS
    required_permission = ("orders", "read")
    serializer_class = DeliverySerializer
    pagination_class = None

    @require_restaurant
    def get_queryset(self):
        qs = Delivery.objects.filter(restaurant=self.request.restaurant).select_related("order", "courier")
        st = self.request.query_params.get("status")
        if st == "open":
            qs = qs.filter(status__in=Delivery.OPEN)
        elif st:
            qs = qs.filter(status__in=st.split(","))
        else:
            qs = qs.filter(created_at__gte=timezone.now() - timezone.timedelta(days=1))
        if self.request.query_params.get("mine") in ("1", "true"):
            qs = qs.filter(courier__staff__user=self.request.user)
        return qs.order_by("-created_at")[:200]


def _order(request, order_id) -> Order:
    return get_object_or_404(Order.objects.select_related("restaurant"), pk=order_id, restaurant=request.restaurant)


@extend_schema(tags=[TAG], responses=DeliverySerializer)
class OrderDeliveryView(APIView):
    permission_classes = PERMS
    required_permission = ("orders", "read")

    @require_restaurant
    def get(self, request, order_id):
        order = _order(request, order_id)
        d = dispatch.delivery_for(order, create=False)
        if d is None:
            return Response({"detail": "no delivery"}, status=status.HTTP_404_NOT_FOUND)
        return Response(DeliverySerializer(d).data)


@extend_schema(tags=[TAG], request=RequestCourierSerializer, responses=DeliverySerializer)
class RequestCourierView(APIView):
    permission_classes = PERMS
    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request, order_id):
        order = _order(request, order_id)
        s = RequestCourierSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            d = dispatch.request_courier(order, provider=s.validated_data.get("provider"), by=request.user)
        except dispatch.DispatchError as exc:
            return _err(exc.code, exc.message, status.HTTP_409_CONFLICT)
        return Response(DeliverySerializer(d).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG], request=AssignCourierSerializer, responses=DeliverySerializer)
class AssignCourierView(APIView):
    permission_classes = PERMS
    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request, order_id):
        order = _order(request, order_id)
        s = AssignCourierSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        courier = get_object_or_404(Courier, pk=s.validated_data["courier_id"], restaurant=request.restaurant)
        if order.order_type != "delivery":
            return _err("not_delivery", "Only delivery orders need a courier.", status.HTTP_409_CONFLICT)
        d = dispatch.delivery_for(order)
        try:
            d = dispatch.assign_own(d, courier, by=request.user)
        except dispatch.DispatchError as exc:
            return _err(exc.code, exc.message, status.HTTP_409_CONFLICT)
        return Response(DeliverySerializer(d).data)


@extend_schema(tags=[TAG], request=CourierUpdateSerializer, responses=DeliverySerializer)
class CourierUpdateView(APIView):
    """Riders move their own deliveries (orders:read is enough for the assigned courier); managers move any."""

    permission_classes = PERMS
    required_permission = ("orders", "read")

    @require_restaurant
    def post(self, request, order_id):
        order = _order(request, order_id)
        d = dispatch.delivery_for(order, create=False)
        if d is None:
            return Response({"detail": "no delivery"}, status=status.HTTP_404_NOT_FOUND)
        mine = d.courier_id and d.courier.staff_id and d.courier.staff.user_id == request.user.pk
        if not mine and not staff_can(request, "orders", "update"):
            return _err("forbidden", "Only the assigned courier or a manager can do that.", status.HTTP_403_FORBIDDEN)
        s = CourierUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        try:
            dispatch.courier_update(d, v["status"], by=request.user, lat=v.get("lat"), lng=v.get("lng"), note=v["note"])
        except dispatch.DispatchError as exc:
            return _err(exc.code, exc.message, status.HTTP_409_CONFLICT)
        return Response(DeliverySerializer(d).data)


@extend_schema(tags=[TAG], request=CancelCourierSerializer, responses=DeliverySerializer)
class CancelCourierView(APIView):
    permission_classes = PERMS
    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request, order_id):
        order = _order(request, order_id)
        d = dispatch.delivery_for(order, create=False)
        if d is None:
            return Response({"detail": "no delivery"}, status=status.HTTP_404_NOT_FOUND)
        s = CancelCourierSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        dispatch.cancel_courier(d, reason=s.validated_data["reason"], by=request.user)
        return Response(DeliverySerializer(d).data)


@extend_schema(tags=[TAG])
class DomainListView(generics.ListCreateAPIView):
    permission_classes = PERMS
    required_permission = ("settings", "update")
    serializer_class = RestaurantDomainSerializer
    pagination_class = None

    @require_restaurant
    def get_queryset(self):
        return RestaurantDomain.objects.filter(restaurant=self.request.restaurant)

    def perform_create(self, serializer):
        row = serializer.save(restaurant=self.request.restaurant)
        domains.verify(row)


@extend_schema(tags=[TAG])
class DomainDetailView(generics.RetrieveDestroyAPIView):
    permission_classes = PERMS
    required_permission = ("settings", "update")
    serializer_class = RestaurantDomainSerializer
    lookup_url_kwarg = "domain_id"

    @require_restaurant
    def get_queryset(self):
        return RestaurantDomain.objects.filter(restaurant=self.request.restaurant)


@extend_schema(tags=[TAG], responses=RestaurantDomainSerializer)
class DomainVerifyView(APIView):
    permission_classes = PERMS
    required_permission = ("settings", "update")

    @require_restaurant
    def post(self, request, domain_id):
        row = get_object_or_404(RestaurantDomain, pk=domain_id, restaurant=request.restaurant)
        domains.verify(row)
        return Response(RestaurantDomainSerializer(row).data)
