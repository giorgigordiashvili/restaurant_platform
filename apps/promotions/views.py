"""Dashboard API (POS / SPA): promotions list, '86 today', promo code on an order; public: validate a code."""

from __future__ import annotations

from decimal import Decimal

from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired
from apps.menu.models import MenuItem
from apps.orders.models import Order
from apps.orders.serializers import OrderSerializer, PromoCodeSerializer
from apps.promotions import services
from apps.promotions.availability import availability, local_now
from apps.promotions.models import Promotion
from apps.promotions.serializers import (
    ItemAvailabilitySerializer,
    PromotionSerializer,
    SetUnavailableSerializer,
    ValidateCodeResultSerializer,
    ValidateCodeSerializer,
)
from apps.tenants.models import Restaurant

TAG = "Dashboard - Promotions"
PERMS = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("promotions")]


def _err(exc, http=status.HTTP_400_BAD_REQUEST):
    return Response({"success": False, "error": {"code": exc.code, "message": exc.message}}, status=http)


@extend_schema(tags=[TAG], responses=PromotionSerializer(many=True))
class PromotionListView(APIView):
    permission_classes = PERMS
    required_permission = ("menu", "read")

    @require_restaurant
    def get(self, request):
        rows = Promotion.objects.filter(restaurant=request.restaurant).select_related("schedule")
        ctx = {"local_now": local_now(request.restaurant)}
        return Response(PromotionSerializer(rows, many=True, context=ctx).data)


@extend_schema(tags=[TAG], request=SetUnavailableSerializer, responses=ItemAvailabilitySerializer)
class ItemUnavailableView(APIView):
    """'86 today': hide a dish from ordering until midnight (or tomorrow), or put it back."""

    permission_classes = PERMS
    required_permission = ("menu", "update")

    @require_restaurant
    def post(self, request, item_id):
        item = get_object_or_404(
            MenuItem.objects.select_related("restaurant", "schedule", "category__schedule"),
            pk=item_id,
            restaurant=request.restaurant,
        )
        ser = SetUnavailableSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        until = ser.validated_data["until"]
        services.set_unavailable(item, None if until == "clear" else until, by=request.user)
        ok, reason = availability(item, restaurant=item.restaurant)
        return Response(
            ItemAvailabilitySerializer(
                {
                    "id": item.pk,
                    "name": item.safe_translation_getter("name", any_language=True),
                    "available_now": ok,
                    "available_from": reason,
                    "unavailable_until": item.unavailable_until,
                    "is_available": item.is_available,
                }
            ).data
        )


@extend_schema(tags=[TAG], responses=ItemAvailabilitySerializer(many=True))
class UnavailableListView(APIView):
    """Dishes currently 86'd (for the POS list)."""

    permission_classes = PERMS
    required_permission = ("menu", "read")

    @require_restaurant
    def get(self, request):
        from django.utils import timezone

        rows = MenuItem.objects.filter(
            restaurant=request.restaurant, unavailable_until__gt=timezone.now()
        ).select_related("restaurant", "schedule", "category__schedule")
        out = []
        for item in rows:
            ok, reason = availability(item, restaurant=item.restaurant)
            out.append(
                {
                    "id": item.pk,
                    "name": item.safe_translation_getter("name", any_language=True),
                    "available_now": ok,
                    "available_from": reason,
                    "unavailable_until": item.unavailable_until,
                    "is_available": item.is_available,
                }
            )
        return Response(ItemAvailabilitySerializer(out, many=True).data)


@extend_schema(tags=[TAG], request=PromoCodeSerializer, responses=OrderSerializer)
class OrderPromoCodeView(APIView):
    """POST applies a code to an open order; DELETE removes promo-code discounts."""

    permission_classes = PERMS
    required_permission = ("orders", "update")

    @require_restaurant
    def post(self, request, order_id):
        order = get_object_or_404(Order, pk=order_id, restaurant=request.restaurant)
        ser = PromoCodeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            services.redeem_code(order, ser.validated_data["code"], by=request.user, channel="pos")
        except services.PromotionError as exc:
            return _err(exc)
        except Exception as exc:  # OrderError (closed / paid)
            code = getattr(exc, "code", "order_error")
            return Response({"success": False, "error": {"code": code, "message": str(exc)}}, status=409)
        order.refresh_from_db()
        return Response(OrderSerializer(order).data)

    @require_restaurant
    def delete(self, request, order_id):
        order = get_object_or_404(Order, pk=order_id, restaurant=request.restaurant)
        try:
            services.remove_code(order, by=request.user)
        except Exception as exc:  # OrderError
            code = getattr(exc, "code", "order_error")
            return Response({"success": False, "error": {"code": code, "message": str(exc)}}, status=409)
        order.refresh_from_db()
        return Response(OrderSerializer(order).data)


class ValidateThrottle(AnonRateThrottle):
    rate = "30/minute"


@extend_schema(tags=["Promotions"], request=ValidateCodeSerializer, responses=ValidateCodeResultSerializer)
class PublicValidateCodeView(APIView):
    """Checkout preview: is this code valid for these items, and how much does it take off?"""

    permission_classes = [AllowAny]
    throttle_classes = [ValidateThrottle]

    def post(self, request, slug):
        restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
        ser = ValidateCodeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            promo, _preview = services.check_code(
                restaurant,
                data["code"],
                customer=request.user if request.user.is_authenticated else None,
                phone=data.get("phone", ""),
                channel=data.get("channel", "web"),
            )
        except services.PromotionError as exc:
            return Response(
                ValidateCodeResultSerializer(
                    {
                        "valid": False,
                        "code": data["code"].upper(),
                        "name": "",
                        "mode": "",
                        "value": None,
                        "discount": None,
                        "error": exc.message,
                        "error_code": exc.code,
                    }
                ).data
            )
        # estimate on the submitted basket
        base = Decimal("0")
        ids = [str(i.get("menu_item_id")) for i in data.get("items", []) if i.get("menu_item_id")]
        items = {str(m.pk): m for m in MenuItem.objects.filter(restaurant=restaurant, pk__in=ids)}
        cats, item_ids = services._targets(promo)
        for row in data.get("items", []):
            m = items.get(str(row.get("menu_item_id")))
            if m is None:
                continue
            qty = max(int(row.get("quantity") or 1), 1)
            if promo.applies_to_item(m, category_ids=cats, item_ids=item_ids):
                base += Decimal(m.price) * qty
        discount = promo.discount_for(base) if base else None
        return Response(
            ValidateCodeResultSerializer(
                {
                    "valid": True,
                    "code": promo.code,
                    "name": promo.name,
                    "mode": promo.mode,
                    "value": promo.value,
                    "discount": discount,
                    "error": "",
                    "error_code": "",
                }
            ).data
        )
