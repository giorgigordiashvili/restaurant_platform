"""Dashboard API (POS sell / lookup / redeem / void), public balance check + digital card page."""

from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiParameter, extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired, staff_can
from apps.giftcards import services
from apps.giftcards.models import GiftCard
from apps.giftcards.serializers import (
    AdjustSerializer,
    GiftCardLookupSerializer,
    GiftCardSerializer,
    GiftCardTransactionSerializer,
    PublicBalanceSerializer,
    RedeemSerializer,
    SellSerializer,
    SummarySerializer,
    VoidSerializer,
)
from apps.orders.models import Order
from apps.tables.models import TableSession
from apps.tenants.models import Restaurant

TAG = "Dashboard - Gift cards"
PERMS = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("gift_cards")]


def _err(exc, http=status.HTTP_409_CONFLICT):
    return Response({"success": False, "error": {"code": exc.code, "message": exc.message, **exc.extra}}, status=http)


def _lookup_payload(card: GiftCard | None, error_code: str = "") -> dict:
    if card is None:
        return {
            "id": None,
            "masked_code": "",
            "balance": "0.00",
            "currency": "GEL",
            "status": "",
            "is_usable": False,
            "expires_at": None,
            "error_code": error_code,
        }
    return {
        "id": card.pk,
        "masked_code": card.masked_code,
        "balance": card.balance,
        "currency": card.currency,
        "status": card.status,
        "is_usable": card.is_usable,
        "expires_at": card.expires_at,
        "error_code": error_code,
    }


@extend_schema(tags=[TAG], responses=SummarySerializer)
class SummaryView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request):
        return Response(SummarySerializer(services.summary(request.restaurant)).data)


@extend_schema(tags=[TAG])
class GiftCardListView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "read")

    @extend_schema(
        parameters=[OpenApiParameter("q", str), OpenApiParameter("status", str)],
        responses=GiftCardSerializer(many=True),
    )
    @require_restaurant
    def get(self, request):
        qs = GiftCard.objects.filter(restaurant=request.restaurant)
        q = request.query_params.get("q", "").strip()
        if q:
            from django.db.models import Q

            qs = qs.filter(
                Q(code__icontains=q.upper())
                | Q(recipient_name__icontains=q)
                | Q(purchaser_name__icontains=q)
                | Q(recipient_phone__icontains=q)
            )
        st = request.query_params.get("status")
        if st:
            qs = qs.filter(status=st)
        return Response(GiftCardSerializer(qs[:100], many=True).data)


@extend_schema(tags=[TAG], request=SellSerializer, responses=GiftCardSerializer)
class SellView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "create")

    @require_restaurant
    def post(self, request):
        s = SellSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        try:
            card = services.sell_pos(
                request.restaurant,
                v["amount"],
                method=v["method"],
                by=request.user,
                tendered=v.get("tendered"),
                kind=v["kind"],
                design=v.get("design", "classic"),
                purchaser={"name": v["purchaser_name"], "phone": v["purchaser_phone"]},
                recipient={"name": v["recipient_name"], "phone": v["recipient_phone"], "email": v["recipient_email"]},
                message=v["message"],
            )
        except services.GiftCardError as exc:
            return _err(exc, status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # LedgerError (shift required etc.)
            code = getattr(exc, "code", "ledger_error")
            return Response({"success": False, "error": {"code": code, "message": str(exc)}}, status=409)
        return Response(GiftCardSerializer(card).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG], parameters=[OpenApiParameter("code", str)], responses=GiftCardLookupSerializer)
class LookupView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request):
        try:
            card = services.lookup(request.restaurant, request.query_params.get("code", ""))
        except services.GiftCardError as exc:
            return Response(
                GiftCardLookupSerializer(_lookup_payload(None, exc.code)).data, status=status.HTTP_404_NOT_FOUND
            )
        try:
            services.check_usable(card)
            code = ""
        except services.GiftCardError as exc:
            code = exc.code
        return Response(GiftCardLookupSerializer(_lookup_payload(card, code)).data)


@extend_schema(tags=[TAG], request=RedeemSerializer, responses={200: dict})
class RedeemView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "create")

    @require_restaurant
    def post(self, request):
        s = RedeemSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        try:
            card = services.lookup(request.restaurant, v["code"])
        except services.GiftCardError as exc:
            return _err(exc, status.HTTP_404_NOT_FOUND)
        kwargs = {}
        if v.get("order_id"):
            kwargs["order"] = get_object_or_404(Order, pk=v["order_id"], restaurant=request.restaurant)
        elif v.get("order_ids"):
            kwargs["orders"] = list(Order.objects.filter(pk__in=v["order_ids"], restaurant=request.restaurant))
        else:
            kwargs["session"] = get_object_or_404(
                TableSession, pk=v["session_id"], table__restaurant=request.restaurant
            )
        try:
            payment = services.redeem(card, v["amount"], by=request.user, **kwargs)
        except services.GiftCardError as exc:
            return _err(exc)
        except Exception as exc:  # LedgerError
            code = getattr(exc, "code", "ledger_error")
            return Response(
                {"success": False, "error": {"code": code, "message": str(exc), **getattr(exc, "extra", {})}},
                status=409,
            )
        from apps.payments import services as ledger
        from apps.payments.serializers import PaymentSerializer

        card.refresh_from_db()
        target_orders = kwargs.get("orders") or (
            [kwargs["order"]] if kwargs.get("order") else ledger.unpaid_orders(kwargs["session"])
        )
        remaining = sum((ledger.balance(o) for o in target_orders), 0)
        return Response(
            {
                "payment": PaymentSerializer(payment).data,
                "receipt_number": payment.receipt_number,
                "balance": str(remaining),
                "card_balance": str(card.balance),
                "change": "0.00",
                "paid_order_numbers": [o.order_number for o in target_orders if ledger.is_paid(o)],
            }
        )


class _CardView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "update")

    def _card(self, request, card_id) -> GiftCard:
        return get_object_or_404(GiftCard, pk=card_id, restaurant=request.restaurant)


@extend_schema(tags=[TAG], responses=GiftCardSerializer)
class GiftCardDetailView(_CardView):
    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request, card_id):
        card = self._card(request, card_id)
        data = GiftCardSerializer(card).data
        data["transactions"] = GiftCardTransactionSerializer(card.transactions.all()[:50], many=True).data
        return Response(data)


@extend_schema(tags=[TAG], request=VoidSerializer, responses=GiftCardSerializer)
class VoidView(_CardView):
    @require_restaurant
    def post(self, request, card_id):
        s = VoidSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        card = services.void(self._card(request, card_id), by=request.user, note=s.validated_data["note"])
        return Response(GiftCardSerializer(card).data)


@extend_schema(tags=[TAG], request=AdjustSerializer, responses=GiftCardSerializer)
class AdjustView(_CardView):
    @require_restaurant
    def post(self, request, card_id):
        s = AdjustSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            card = services.adjust(
                self._card(request, card_id), s.validated_data["amount"], by=request.user, note=s.validated_data["note"]
            )
        except services.GiftCardError as exc:
            return _err(exc)
        return Response(GiftCardSerializer(card).data)


@extend_schema(tags=[TAG], request=None, responses=GiftCardSerializer)
class ResendView(_CardView):
    @require_restaurant
    def post(self, request, card_id):
        card = self._card(request, card_id)
        if not services.deliver_digital(card, by=request.user, force=True):
            return Response({"success": False, "error": {"code": "no_recipient"}}, status=409)
        card.refresh_from_db()
        return Response(GiftCardSerializer(card).data)


# ── public ─────────────────────────────────────────────────────────────────


class PublicThrottle(AnonRateThrottle):
    scope = "gift_card_public"
    rate = "20/min"


@extend_schema(tags=["Gift cards"], parameters=[OpenApiParameter("code", str)], responses=PublicBalanceSerializer)
class PublicBalanceView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PublicThrottle]

    def get(self, request, slug):
        restaurant = get_object_or_404(Restaurant, slug=slug, is_active=True)
        if not services.enabled(restaurant):
            return Response({"success": False, "error": {"code": "module_disabled"}}, status=404)
        try:
            card = services.lookup(restaurant, request.query_params.get("code", ""))
        except services.GiftCardError as exc:
            return Response({"success": False, "error": {"code": exc.code, "message": exc.message}}, status=404)
        return Response(
            {
                "success": True,
                "data": {
                    "masked_code": card.masked_code,
                    "balance": str(card.balance),
                    "currency": card.currency,
                    "status": card.status,
                    "expires_at": card.expires_at,
                    "restaurant": restaurant.name,
                },
            }
        )


@extend_schema(tags=["Gift cards"], responses={200: dict})
class PublicCardView(APIView):
    """The digital card page linked from the SMS / email (JSON for the site, HTML fallback)."""

    permission_classes = [AllowAny]
    throttle_classes = [PublicThrottle]

    def get(self, request, token):
        card = get_object_or_404(GiftCard.objects.select_related("restaurant"), token=token)
        data = {
            "restaurant": card.restaurant.name,
            "restaurant_slug": card.restaurant.slug,
            "code": card.code,
            "initial_value": str(card.initial_value),
            "balance": str(card.balance),
            "currency": card.currency,
            "status": card.status,
            "design": card.design,
            "recipient_name": card.recipient_name,
            "purchaser_name": card.purchaser_name,
            "message": card.message,
            "expires_at": card.expires_at,
        }
        if "text/html" in request.headers.get("Accept", "") and request.query_params.get("format") != "json":
            html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{card.restaurant.name} · gift card</title><style>body{{font-family:system-ui,sans-serif;max-width:480px;margin:40px auto;padding:0 20px;text-align:center;color:#111}}
.card{{border-radius:20px;padding:28px;background:linear-gradient(135deg,#EC003F,#7c3aed);color:#fff}} code{{font-size:26px;letter-spacing:3px;display:block;margin:12px 0}}</style></head>
<body><div class="card"><h2>{card.restaurant.name}</h2><p>{_('Gift card')}</p><strong style="font-size:34px">{card.balance} {card.currency}</strong><code>{card.code}</code>
{f'<p>{card.message}</p>' if card.message else ''}</div><p>{_('Show this code at the restaurant or type it at online checkout.')}</p></body></html>"""
            return HttpResponse(html)
        return Response({"success": True, "data": data})
