"""
Views for Bank of Georgia Payment Manager integration.

Frontend → ``POST /api/v1/payments/bog/initiate/`` with ``{target: 'order' |
'reservation', ..._payload, return_url}``. Response includes ``redirect_url``
pointing at ``payment.bog.ge`` — the frontend performs ``window.location.assign``.

BOG → ``POST /api/v1/payments/bog/webhook/`` after any state change. We verify
the ``Callback-Signature`` header against BOG's public key and fan state out to
Order / Reservation / PaymentMethod.

The frontend also polls ``GET /api/v1/payments/bog/status/<bog_order_id>/`` on
the return-landing page, as a belt-and-braces fallback for dropped webhooks.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.menu.models import MenuItem
from apps.orders.models import Order, OrderItem, OrderItemModifier, OrderStatusHistory
from apps.reservations.models import Reservation
from apps.reservations.serializers import ReservationDetailSerializer
from apps.tables.models import Table
from apps.tenants.models import Restaurant

from ..models import BogTransaction, Payment, PaymentMethod
from .client import BogClientError, get_client
from .serializers import (
    BogStatusResponseSerializer,
    InitiateAddCardSerializer,
    InitiatePaymentSerializer,
)
from .signatures import SignatureError, verify_signature

logger = logging.getLogger(__name__)


# ── Shared helpers ──────────────────────────────────────────────────────────


def _assert_configured() -> Response | None:
    """Return a 503 response if BOG credentials aren't set."""
    if not settings.BOG_CLIENT_ID or not settings.BOG_CLIENT_SECRET:
        return Response(
            {
                "success": False,
                "error": {
                    "code": "bog_unconfigured",
                    "message": "BOG payment gateway credentials are not configured.",
                },
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return None


def _build_basket(order: Order) -> list[dict[str, Any]]:
    """Translate OrderItem rows into BOG basket entries."""
    basket: list[dict[str, Any]] = []
    for item in order.items.all():
        basket.append(
            {
                "product_id": str(item.menu_item_id or item.id),
                "description": item.item_name,
                "quantity": item.quantity or 1,
                "unit_price": float(item.unit_price),
                "total_price": float(item.total_price),
            }
        )
    return basket


def _reservation_basket(reservation: Reservation, amount: Decimal) -> list[dict[str, Any]]:
    return [
        {
            "product_id": f"reservation-{reservation.confirmation_code}",
            "description": f"Reservation deposit — {reservation.guest_name}",
            "quantity": 1,
            "unit_price": float(amount),
            "total_price": float(amount),
        }
    ]


def _add_card_basket(amount: Decimal) -> list[dict[str, Any]]:
    return [
        {
            "product_id": "tokenisation",
            "description": "Card tokenisation (pre-authorisation)",
            "quantity": 1,
            "unit_price": float(amount),
            "total_price": float(amount),
        }
    ]


def _resolve_restaurant(slug: str) -> Restaurant:
    try:
        return Restaurant.objects.get(slug=slug, is_active=True)
    except Restaurant.DoesNotExist as exc:
        raise ValueError(f"Restaurant '{slug}' not found") from exc


def _split_config(restaurant: Restaurant | None, amount: Decimal) -> dict[str, Any] | None:
    """
    Build the ``config.split`` block BOG attaches to an ecommerce order.

    Returns ``None`` when the restaurant hasn't opted into BOG split payouts
    (either the flag is off or the IBAN is blank); callers should then omit
    ``config`` from the payload entirely — BOG treats missing config as "send
    everything to the master account", which is also our pre-split behaviour
    and keeps existing merchants working while we onboard IBANs.
    """
    from apps.payments.splits import compute_split

    if restaurant is None:
        return None
    if not restaurant.accepts_bog_payments or not restaurant.bog_payout_iban.strip():
        return None

    split = compute_split(amount, restaurant)
    if split.restaurant_amount <= 0:
        return None

    return {
        "split": {
            "split_payments": [
                {
                    "iban": restaurant.bog_payout_iban.strip(),
                    "amount": float(split.restaurant_amount),
                    "description": f"{restaurant.name} share",
                }
            ]
        }
    }


def _redirect_urls(return_url: str, external_order_id: str) -> dict[str, str]:
    """
    BOG redirects the browser back to whatever ``redirect_urls.success`` / ``fail``
    we pass, verbatim. We inject our ``external_order_id`` + ``status`` so the
    frontend's return landing page can poll status without needing to remember
    which order it just kicked off. We use the external id (not BOG's order_id)
    because BOG generates their id only after we send this body — the external
    id we know up-front.
    """
    from urllib.parse import urlencode, urlparse, urlunparse

    parsed = urlparse(return_url)

    def _with(status_value: str) -> str:
        extra = urlencode({"ref": external_order_id, "status": status_value})
        joined = f"{parsed.query}&{extra}" if parsed.query else extra
        return urlunparse(parsed._replace(query=joined))

    return {"success": _with("ok"), "fail": _with("fail")}


def _callback_url(request: Request) -> str:
    """
    Absolute URL BOG will POST the webhook to.

    Prefers the ``BOG_WEBHOOK_URL`` env override so sandbox tests can point the
    callback at a tunnel (ngrok, Cloudflare Tunnel, etc.) that forwards to a
    locally-running backend. In production this is normally unset and we fall
    back to deriving the URL from the incoming request host.
    """
    override = getattr(settings, "BOG_WEBHOOK_URL", "") or ""
    if override:
        return override.rstrip("/")
    return request.build_absolute_uri("/api/v1/payments/bog/webhook/")


# ── Initiate: order ─────────────────────────────────────────────────────────


@extend_schema(tags=["BOG Payments"])
class InitiatePaymentView(APIView):
    """
    POST /api/v1/payments/bog/initiate/

    Creates an Order or Reservation (status=pending_payment), calls BOG to
    create an ecommerce order, persists a BogTransaction row, and returns the
    hosted-page redirect URL.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        unconfigured = _assert_configured()
        if unconfigured:
            return unconfigured

        serializer = InitiatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target = data["target"]

        try:
            if target == InitiatePaymentSerializer.TARGET_ORDER:
                return self._initiate_order(request, data["order_payload"], data["return_url"])
            if target == InitiatePaymentSerializer.TARGET_SESSION:
                return self._initiate_session_settle(request, data["session_payload"], data["return_url"])
            return self._initiate_reservation(request, data["reservation_payload"], data["return_url"])
        except ValueError as exc:
            return Response(
                {"success": False, "error": {"message": str(exc)}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except BogClientError as exc:
            # Log the BOG response body — without it "returned 400" is
            # unhelpful. The payload contains BOG's field-level validation
            # messages that tell us exactly which key was rejected.
            logger.exception(
                "BOG upstream call failed during initiate (%s) status=%s payload=%r request=%r",
                target,
                getattr(exc, "status_code", None),
                getattr(exc, "payload", None),
                data,
            )
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "bog_upstream",
                        "message": "Payment gateway is temporarily unavailable. Please try again.",
                        "detail": str(exc),
                    },
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except ImproperlyConfigured as exc:
            logger.error("BOG misconfigured: %s", exc)
            return Response(
                {"success": False, "error": {"message": str(exc)}},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    # ── Helpers ────────────────────────────────────────────────────────────

    @transaction.atomic
    def _initiate_order(self, request: Request, payload: dict[str, Any], return_url: str) -> Response:
        restaurant = _resolve_restaurant(payload["restaurant_slug"])

        # Belt-and-braces for the master ordering switch — the customer site
        # already hides all order UI when this flag is off, but block here
        # too so raw API clients can't bypass it.
        if not restaurant.accepts_remote_orders:
            raise ValueError("Ordering is disabled at this restaurant.")

        table = None
        if payload.get("table_id"):
            try:
                table = Table.objects.get(id=payload["table_id"], restaurant=restaurant)
            except Table.DoesNotExist as exc:
                raise ValueError("Table not found") from exc

        session_id = payload.get("table_session")
        if session_id:
            from apps.tables.models import TableSession

            try:
                session_obj = TableSession.objects.get(id=session_id, table__restaurant=restaurant)
            except TableSession.DoesNotExist as exc:
                raise ValueError("Table session not found.") from exc
            if session_obj.status != "active":
                raise ValueError("This table session has ended. Scan a new QR to start over.")

        order = Order.objects.create(
            restaurant=restaurant,
            table=table,
            table_session_id=session_id,
            customer=request.user if request.user.is_authenticated else None,
            order_type=payload.get("order_type", "dine_in"),
            tip_amount=payload.get("tip_amount", 0),
            status="pending_payment",
            customer_name=payload.get("customer_name", ""),
            customer_phone=payload.get("customer_phone", ""),
            customer_email=payload.get("customer_email", ""),
            customer_notes=payload.get("customer_notes", ""),
            delivery_address=payload.get("delivery_address", ""),
        )

        for item_payload in payload["items"]:
            menu_item: MenuItem = item_payload["menu_item_id"]
            if menu_item.restaurant_id != restaurant.id:
                raise ValueError("One or more items don't belong to this restaurant.")

            order_item = OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                item_name=menu_item.safe_translation_getter("name", default=f"Item {menu_item.pk}"),
                item_description=menu_item.safe_translation_getter("description", default=""),
                unit_price=menu_item.price,
                quantity=item_payload.get("quantity", 1),
                total_price=menu_item.price * item_payload.get("quantity", 1),
                preparation_station=menu_item.preparation_station,
                special_instructions=item_payload.get("special_instructions", ""),
            )

            for modifier in item_payload.get("modifier_ids", []):
                OrderItemModifier.objects.create(
                    order_item=order_item,
                    modifier=modifier,
                    modifier_name=modifier.safe_translation_getter("name", default=f"Modifier {modifier.pk}"),
                    price_adjustment=modifier.price_adjustment,
                )

            order_item.recalculate_total()

        order.calculate_totals()

        # Apply a platform-loyalty tier discount when the customer is
        # authenticated, carries a tier with non-zero discount, and the
        # restaurant has opted into the program. The discount is written
        # to `order.discount_amount`; calculate_totals reruns so
        # `order.total` (and therefore the BOG charge below) reflects it.
        try:
            from apps.loyalty.services import current_user_tier

            if request.user.is_authenticated and restaurant.accepts_platform_loyalty:
                tier = current_user_tier(request.user)
                if tier and tier.discount_percent > 0:
                    pct = Decimal(tier.discount_percent) / Decimal(100)
                    order.discount_amount = (order.subtotal * pct).quantize(Decimal("0.01"))
                    order.calculate_totals()
        except Exception:
            logger.exception("Failed to apply platform loyalty discount")

        OrderStatusHistory.objects.create(
            order=order,
            from_status="",
            to_status="pending_payment",
            notes="Order awaiting BOG payment confirmation.",
        )

        amount = order.total
        if amount <= 0:
            raise ValueError("Order total must be greater than zero.")

        bog_payload = {
            "callback_url": _callback_url(request),
            "external_order_id": order.order_number,
            "payment_method": ["card"],
            "purchase_units": {
                "currency": "GEL",
                "total_amount": float(amount),
                "basket": _build_basket(order),
            },
            "redirect_urls": _redirect_urls(return_url, order.order_number),
        }
        split_config = _split_config(order.restaurant, Decimal(str(amount)))
        if split_config:
            bog_payload["config"] = split_config
        response = get_client().create_order(
            bog_payload,
            idempotency_key=str(order.id),
        )
        bog_order_id = response.get("id")
        redirect_url = (response.get("_links") or {}).get("redirect", {}).get("href")
        if not bog_order_id or not redirect_url:
            raise BogClientError("BOG response missing id/redirect.href", payload=response)

        transaction_row = BogTransaction.objects.create(
            bog_order_id=bog_order_id,
            external_order_id=order.order_number,
            flow_type=BogTransaction.FLOW_ORDER,
            order=order,
            initiated_by=request.user if request.user.is_authenticated else None,
            amount=amount,
            currency="GEL",
            status=BogTransaction.STATUS_CREATED,
            redirect_url=redirect_url,
            return_url=return_url,
            callback_url=bog_payload["callback_url"],
            request_payload=bog_payload,
            response_payload=response,
        )

        return Response(
            {
                "success": True,
                "data": {
                    "bog_order_id": transaction_row.bog_order_id,
                    "order_number": order.order_number,
                    "redirect_url": redirect_url,
                },
            },
            status=status.HTTP_201_CREATED,
        )

    @transaction.atomic
    def _initiate_session_settle(self, request: Request, payload: dict[str, Any], return_url: str) -> Response:
        """
        Host "pay for the whole table" flow. Charges a single BOG transaction
        for every currently-unpaid non-cancelled order on the session, plus
        an optional tip, and snapshots the covered orders via the M2M so
        orders placed after this call still require their own payment.
        """
        from apps.tables.models import TableSession

        restaurant = _resolve_restaurant(payload["restaurant_slug"])

        try:
            session_obj = TableSession.objects.get(id=payload["session_id"], table__restaurant=restaurant)
        except TableSession.DoesNotExist as exc:
            raise ValueError("Table session not found.") from exc
        if session_obj.status != "active":
            raise ValueError("This table session has ended. Scan a new QR to start over.")

        # Identify orders the settle will pay off: not cancelled AND not
        # already paid by an individual BOG charge AND not already covered
        # by a previous completed settle txn.
        orders = list(session_obj.orders.prefetch_related("bog_transactions", "settle_transactions").all())
        unpaid_orders = [
            o
            for o in orders
            if o.status != "cancelled"
            and not any(t.status == "completed" for t in o.bog_transactions.all())
            and not any(t.status == "completed" for t in o.settle_transactions.all())
        ]
        if not unpaid_orders:
            raise ValueError("All orders on this table are already paid.")

        # Apply platform-loyalty tier discount to each unpaid order
        # before totalling. Mirrors the _initiate_order path so the
        # single-order and whole-table-settle flows behave identically.
        try:
            from apps.loyalty.services import current_user_tier

            if request.user.is_authenticated and restaurant.accepts_platform_loyalty:
                tier = current_user_tier(request.user)
                if tier and tier.discount_percent > 0:
                    pct = Decimal(tier.discount_percent) / Decimal(100)
                    for o in unpaid_orders:
                        o.discount_amount = (o.subtotal * pct).quantize(Decimal("0.01"))
                        o.calculate_totals()
        except Exception:
            logger.exception("Failed to apply platform loyalty discount (session settle)")

        tip_amount = Decimal(payload.get("tip_amount") or 0)
        orders_total = sum((o.total or Decimal("0")) for o in unpaid_orders)
        amount = orders_total + tip_amount
        if amount <= 0:
            raise ValueError("Settle total must be greater than zero.")

        external_id = f"settle-{session_obj.id.hex[:12]}-{uuid.uuid4().hex[:6]}"
        # BOG requires `total_price` on every basket entry in addition to
        # `unit_price` × `quantity`. Every other basket builder in this
        # module (_build_basket, _reservation_basket, _add_card_basket)
        # emits both; this one was omitting total_price, which the BOG
        # API rejects with a bare 400 and no descriptive payload — the
        # 502 we saw on /table/settle was downstream of that failure.
        basket = [
            {
                "quantity": 1,
                "unit_price": float(o.total or 0),
                "total_price": float(o.total or 0),
                "product_id": o.order_number,
                "description": f"Order {o.order_number}",
            }
            for o in unpaid_orders
        ]
        if tip_amount > 0:
            basket.append(
                {
                    "quantity": 1,
                    "unit_price": float(tip_amount),
                    "total_price": float(tip_amount),
                    "product_id": "tip",
                    "description": "Tip",
                }
            )

        bog_payload = {
            "callback_url": _callback_url(request),
            "external_order_id": external_id,
            "payment_method": ["card"],
            "purchase_units": {
                "currency": "GEL",
                "total_amount": float(amount),
                "basket": basket,
            },
            "redirect_urls": _redirect_urls(return_url, external_id),
        }
        # All orders in a single session belong to one restaurant; use that
        # restaurant's IBAN for the split so the 95 % lands directly.
        split_config = _split_config(restaurant, Decimal(str(amount)))
        if split_config:
            bog_payload["config"] = split_config
        # BOG requires the Idempotency-Key header to be a valid UUID v4.
        # Our `external_id` is a human-readable string (`settle-…-…`), so
        # we mint a fresh UUID for the header and keep external_id for
        # our own ledger + BOG's `external_order_id` field (which has no
        # format restriction).
        idempotency_key = str(uuid.uuid4())
        response = get_client().create_order(
            bog_payload,
            idempotency_key=idempotency_key,
        )
        bog_order_id = response.get("id")
        redirect_url = (response.get("_links") or {}).get("redirect", {}).get("href")
        if not bog_order_id or not redirect_url:
            raise BogClientError("BOG response missing id/redirect.href", payload=response)

        txn = BogTransaction.objects.create(
            bog_order_id=bog_order_id,
            external_order_id=external_id,
            flow_type=BogTransaction.FLOW_SESSION_SETTLE,
            session=session_obj,
            initiated_by=request.user if request.user.is_authenticated else None,
            amount=amount,
            currency="GEL",
            status=BogTransaction.STATUS_CREATED,
            redirect_url=redirect_url,
            return_url=return_url,
            callback_url=bog_payload["callback_url"],
            request_payload=bog_payload,
            response_payload=response,
        )
        txn.covered_orders.set(unpaid_orders)

        return Response(
            {
                "success": True,
                "data": {
                    "bog_order_id": bog_order_id,
                    "session_id": str(session_obj.id),
                    "covered_order_numbers": [o.order_number for o in unpaid_orders],
                    "amount": str(amount),
                    "redirect_url": redirect_url,
                },
            },
            status=status.HTTP_201_CREATED,
        )

    @transaction.atomic
    def _initiate_reservation(self, request: Request, payload: dict[str, Any], return_url: str) -> Response:
        restaurant = _resolve_restaurant(payload["restaurant_slug"])

        reservation = Reservation.objects.create(
            restaurant=restaurant,
            customer=request.user if request.user.is_authenticated else None,
            guest_name=payload["guest_name"],
            guest_email=payload.get("guest_email", ""),
            guest_phone=payload["guest_phone"],
            reservation_date=payload["reservation_date"],
            reservation_time=payload["reservation_time"],
            party_size=payload["party_size"],
            special_requests=payload.get("special_requests", ""),
            status="pending_payment",
            source="website",
        )

        # If the guest added food to their cart before clicking "book with order",
        # create a sibling Order(status=pending_payment) tied to this reservation.
        # Charged together in one BOG transaction below.
        pre_order: Order | None = None
        pre_order_total = Decimal("0")
        items_payload = payload.get("items") or []
        if items_payload:
            pre_order = Order.objects.create(
                restaurant=restaurant,
                reservation=reservation,
                customer=request.user if request.user.is_authenticated else None,
                order_type="dine_in",
                status="pending_payment",
                customer_name=payload["guest_name"],
                customer_phone=payload["guest_phone"],
                customer_email=payload.get("guest_email", ""),
                customer_notes=payload.get("special_requests", ""),
            )
            for item_payload in items_payload:
                menu_item: MenuItem = item_payload["menu_item_id"]
                if menu_item.restaurant_id != restaurant.id:
                    raise ValueError("One or more pre-order items don't belong to this restaurant.")
                order_item = OrderItem.objects.create(
                    order=pre_order,
                    menu_item=menu_item,
                    item_name=menu_item.safe_translation_getter("name", default=f"Item {menu_item.pk}"),
                    item_description=menu_item.safe_translation_getter("description", default=""),
                    unit_price=menu_item.price,
                    quantity=item_payload.get("quantity", 1),
                    total_price=menu_item.price * item_payload.get("quantity", 1),
                    preparation_station=menu_item.preparation_station,
                    special_instructions=item_payload.get("special_instructions", ""),
                )
                for modifier in item_payload.get("modifier_ids", []):
                    OrderItemModifier.objects.create(
                        order_item=order_item,
                        modifier=modifier,
                        modifier_name=modifier.safe_translation_getter("name", default=f"Modifier {modifier.pk}"),
                        price_adjustment=modifier.price_adjustment,
                    )
                order_item.recalculate_total()
            pre_order.calculate_totals()
            OrderStatusHistory.objects.create(
                order=pre_order,
                from_status="",
                to_status="pending_payment",
                notes=f"Pre-order for reservation {reservation.confirmation_code}; awaiting BOG payment.",
            )
            pre_order_total = pre_order.total

        override = payload.get("deposit_amount_override")
        if override is not None:
            deposit = Decimal(str(override))
        else:
            deposit = Decimal(settings.BOG_RESERVATION_DEPOSIT_AMOUNT)
        if deposit <= 0 and pre_order_total <= 0:
            raise ValueError("Reservation deposit must be greater than zero.")

        amount = deposit + pre_order_total
        basket = _reservation_basket(reservation, deposit)
        if pre_order is not None:
            basket.extend(_build_basket(pre_order))

        bog_payload = {
            "callback_url": _callback_url(request),
            "external_order_id": reservation.confirmation_code,
            "payment_method": ["card"],
            "purchase_units": {
                "currency": "GEL",
                "total_amount": float(amount),
                "basket": basket,
            },
            "redirect_urls": _redirect_urls(return_url, reservation.confirmation_code),
        }
        split_config = _split_config(reservation.restaurant, Decimal(str(amount)))
        if split_config:
            bog_payload["config"] = split_config
        response = get_client().create_order(
            bog_payload,
            idempotency_key=str(reservation.id),
        )
        bog_order_id = response.get("id")
        redirect_url = (response.get("_links") or {}).get("redirect", {}).get("href")
        if not bog_order_id or not redirect_url:
            raise BogClientError("BOG response missing id/redirect.href", payload=response)

        BogTransaction.objects.create(
            bog_order_id=bog_order_id,
            external_order_id=reservation.confirmation_code,
            flow_type=BogTransaction.FLOW_RESERVATION,
            reservation=reservation,
            order=pre_order,
            initiated_by=request.user if request.user.is_authenticated else None,
            amount=amount,
            currency="GEL",
            status=BogTransaction.STATUS_CREATED,
            redirect_url=redirect_url,
            return_url=return_url,
            callback_url=bog_payload["callback_url"],
            request_payload=bog_payload,
            response_payload=response,
        )

        return Response(
            {
                "success": True,
                "data": {
                    "bog_order_id": bog_order_id,
                    "reservation_id": str(reservation.id),
                    "confirmation_code": reservation.confirmation_code,
                    "order_number": pre_order.order_number if pre_order else None,
                    "redirect_url": redirect_url,
                    "reservation": ReservationDetailSerializer(reservation).data,
                },
            },
            status=status.HTTP_201_CREATED,
        )


# ── Initiate: add card ──────────────────────────────────────────────────────


@extend_schema(tags=["BOG Payments"])
class InitiateAddCardView(APIView):
    """
    POST /api/v1/payments/methods/add/ (BOG-backed).

    Creates a nominal pre-auth BOG order so we can capture card metadata from
    the resulting receipt. The actual PaymentMethod row is created later in
    the webhook handler, once the receipt reaches ``completed``.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        unconfigured = _assert_configured()
        if unconfigured:
            return unconfigured

        serializer = InitiateAddCardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return_url = serializer.validated_data["return_url"]

        amount = Decimal(settings.BOG_ADD_CARD_AMOUNT)
        if amount <= 0:
            return Response(
                {"success": False, "error": {"message": "Add-card pre-auth amount must be > 0."}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        method_intent_id = uuid.uuid4().hex
        external_order_id = f"addcard-{method_intent_id}"
        bog_payload = {
            "callback_url": _callback_url(request),
            "external_order_id": external_order_id,
            "capture": "manual",  # Pre-auth — we'll void in the webhook later.
            "payment_method": ["card"],
            "purchase_units": {
                "currency": "GEL",
                "total_amount": float(amount),
                "basket": _add_card_basket(amount),
            },
            "redirect_urls": _redirect_urls(return_url, external_order_id),
        }

        try:
            response = get_client().create_order(
                bog_payload,
                idempotency_key=method_intent_id,
            )
        except BogClientError as exc:
            logger.exception("BOG create_order failed during add-card initiate")
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "bog_upstream",
                        "message": "Payment gateway is temporarily unavailable.",
                        "detail": str(exc),
                    },
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        bog_order_id = response.get("id")
        redirect_url = (response.get("_links") or {}).get("redirect", {}).get("href")
        if not bog_order_id or not redirect_url:
            return Response(
                {"success": False, "error": {"message": "BOG response missing id/redirect."}},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        BogTransaction.objects.create(
            bog_order_id=bog_order_id,
            external_order_id=method_intent_id,
            flow_type=BogTransaction.FLOW_ADD_CARD,
            initiated_by=request.user,
            amount=amount,
            currency="GEL",
            status=BogTransaction.STATUS_CREATED,
            redirect_url=redirect_url,
            return_url=return_url,
            callback_url=bog_payload["callback_url"],
            request_payload=bog_payload,
            response_payload=response,
        )

        return Response(
            {
                "success": True,
                "data": {
                    "bog_order_id": bog_order_id,
                    "method_intent_id": method_intent_id,
                    "redirect_url": redirect_url,
                },
            },
            status=status.HTTP_201_CREATED,
        )


# ── Status ──────────────────────────────────────────────────────────────────


@extend_schema(tags=["BOG Payments"])
class BogStatusView(APIView):
    """
    GET /api/v1/payments/bog/status/<bog_order_id>/

    Polled by the frontend's payment-return landing page. If the DB row is
    still in a non-terminal state, we proactively re-fetch the receipt from
    BOG so the user sees something fresh while webhooks may be in flight.
    """

    permission_classes = [AllowAny]

    def get(self, request, bog_order_id: str):
        # Accept either BOG's order_id or our external reference (order_number,
        # confirmation_code, or method_intent_id) so the return-landing page can
        # look up without needing BOG's id — BOG doesn't echo it in the redirect.
        from django.db.models import Q

        try:
            txn = (
                BogTransaction.objects.select_related("order", "reservation", "payment_method")
                .filter(Q(bog_order_id=bog_order_id) | Q(external_order_id=bog_order_id))
                .latest("created_at")
            )
        except BogTransaction.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Transaction not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not txn.is_terminal:
            # Refresh on demand — cheap and avoids relying on webhook timing.
            # BUG FIX: always use txn.bog_order_id (BOG's UUID) here, not the
            # URL parameter — the URL param may be our external reference
            # (confirmation_code / order_number), which BOG doesn't know.
            try:
                receipt = get_client().get_receipt(txn.bog_order_id)
                _apply_receipt(txn, receipt, source="poll")
            except BogClientError:
                logger.warning("BOG receipt fetch failed for %s", txn.bog_order_id)

        data = {
            "status": txn.status,
            "code": txn.code,
            "code_description": txn.code_description,
            "order_number": txn.order.order_number if txn.order else None,
            "reservation_id": str(txn.reservation_id) if txn.reservation_id else None,
            "payment_method_id": str(txn.payment_method_id) if txn.payment_method_id else None,
            "session_id": str(txn.session_id) if txn.session_id else None,
            "flow_type": txn.flow_type,
        }
        return Response(
            {"success": True, "data": BogStatusResponseSerializer(data).data},
            status=status.HTTP_200_OK,
        )


# ── Webhook ─────────────────────────────────────────────────────────────────


@extend_schema(tags=["BOG Payments"])
class BogWebhookView(APIView):
    """
    POST /api/v1/payments/bog/webhook/

    Signed callback from BOG. We verify the signature against BOG's public key,
    then fan out the status change to the linked Order/Reservation/PaymentMethod.
    """

    permission_classes = [AllowAny]
    # DRF wraps the raw body; we need it for signature verification.
    authentication_classes: list = []

    def post(self, request):
        raw_body = request.body
        signature = request.headers.get("Callback-Signature") or request.headers.get("callback-signature")

        try:
            valid = verify_signature(raw_body, signature)
        except SignatureError as exc:
            logger.error("BOG webhook signature configuration error: %s", exc)
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)

        if not valid:
            logger.warning("BOG webhook rejected — bad signature")
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        import json

        try:
            envelope = json.loads(raw_body)
        except ValueError:
            logger.warning("BOG webhook body is not valid JSON")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        body = envelope.get("body") if isinstance(envelope, dict) else None
        if not isinstance(body, dict):
            logger.warning("BOG webhook missing body")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        bog_order_id = body.get("order_id")
        if not bog_order_id:
            logger.warning("BOG webhook missing body.order_id")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        try:
            txn = BogTransaction.objects.select_for_update().get(bog_order_id=bog_order_id)
        except BogTransaction.DoesNotExist:
            logger.warning("BOG webhook for unknown bog_order_id=%s", bog_order_id)
            # Still return 200 — BOG shouldn't retry for unknown orders.
            return Response(status=status.HTTP_200_OK)

        _apply_receipt(txn, body, source="webhook")
        return Response(status=status.HTTP_200_OK)


# ── Shared state-apply helper ────────────────────────────────────────────────


def _apply_receipt(txn: BogTransaction, receipt: dict[str, Any], *, source: str) -> None:
    """
    Project a BOG receipt (from either the webhook body or a poll) onto the
    BogTransaction row and fan out to the linked model.
    """
    order_status = receipt.get("order_status") or {}
    new_status = order_status.get("key") or txn.status
    payment_detail = receipt.get("payment_detail") or {}
    code = payment_detail.get("code")
    code_description = payment_detail.get("code_description") or ""
    reject_reason = receipt.get("reject_reason") or ""

    txn.status = new_status
    if isinstance(code, int):
        txn.code = code
    txn.code_description = code_description
    txn.reject_reason = reject_reason

    if source == "webhook":
        txn.last_webhook_at = timezone.now()
        txn.last_webhook_payload = receipt
    else:
        txn.last_reconciled_at = timezone.now()
        txn.response_payload = {**(txn.response_payload or {}), "last_receipt": receipt}

    # Fan out based on flow_type.
    if txn.flow_type == BogTransaction.FLOW_ORDER and txn.order_id:
        _apply_order_status(txn)
    elif txn.flow_type == BogTransaction.FLOW_RESERVATION and txn.reservation_id:
        _apply_reservation_status(txn)
        # A reservation may have a sibling pre-order — flip its status too so
        # the kitchen sees it as soon as deposit + food are paid.
        if txn.order_id:
            _apply_order_status(txn)
    elif txn.flow_type == BogTransaction.FLOW_ADD_CARD and not txn.payment_method_id:
        _apply_add_card_status(txn, payment_detail)
    elif txn.flow_type == BogTransaction.FLOW_SESSION_SETTLE:
        _apply_session_settle_status(txn)

    # Whenever BOG reports a refund — full or partial — and the original
    # order was split-paid, the restaurant's 95 % share has already landed
    # in their bank account. BOG does not claw that back, so we record a
    # RestaurantDebit (platform admin settles off-platform).
    _maybe_record_refund_debit(txn, receipt)

    txn.save()


def _maybe_record_refund_debit(txn: BogTransaction, receipt: dict[str, Any]) -> None:
    """
    Write a RestaurantDebit row the first time we see a refund status on a
    split-paid transaction. Idempotent — guarded by a presence check on the
    transaction so replayed webhooks don't pile up duplicate debits.
    """
    from apps.payments.models import RestaurantDebit
    from apps.payments.splits import compute_split

    REFUND_STATUSES = {
        BogTransaction.STATUS_REFUNDED,
        BogTransaction.STATUS_REFUNDED_PARTIALLY,
    }
    if txn.status not in REFUND_STATUSES:
        return

    restaurant = None
    if txn.order_id and txn.order:
        restaurant = txn.order.restaurant
    elif txn.reservation_id and txn.reservation:
        restaurant = txn.reservation.restaurant
    elif txn.session_id and txn.session:
        restaurant = txn.session.restaurant
    if restaurant is None or not restaurant.accepts_bog_payments:
        return  # No split was configured → nothing on the restaurant side to claw back.

    if RestaurantDebit.objects.filter(
        source_bog_transaction=txn,
        source=RestaurantDebit.SOURCE_BOG_REFUND,
    ).exists():
        return  # Already recorded on an earlier webhook pass.

    # Prefer the refunded amount BOG stamps on the receipt; fall back to the
    # original transaction total if BOG didn't include a refunded_amount field.
    payment_detail = receipt.get("payment_detail") or {}
    refunded_amount_raw = payment_detail.get("refunded_amount") or receipt.get("refunded_amount")
    try:
        refunded_amount = Decimal(str(refunded_amount_raw)) if refunded_amount_raw else txn.amount
    except Exception:
        refunded_amount = txn.amount
    if refunded_amount <= 0:
        return

    split = compute_split(refunded_amount, restaurant)
    if split.restaurant_amount <= 0:
        return

    # Reverse referral credit + refund any wallet spent on the order. Both are
    # idempotent so the webhook-retry safety of this whole branch is preserved.
    if txn.order_id and txn.order:
        try:
            from apps.referrals.services import clawback_referral, refund_wallet_spend

            clawback_referral(txn.order)
            refund_wallet_spend(txn.order)
        except Exception:  # pragma: no cover
            logger.exception("Referral clawback / wallet refund failed for order %s", txn.order_id)

    RestaurantDebit.objects.create(
        restaurant=restaurant,
        amount=split.restaurant_amount,
        currency=txn.currency or "GEL",
        source=RestaurantDebit.SOURCE_BOG_REFUND,
        source_bog_transaction=txn,
        notes=(
            f"BOG refund of {refunded_amount} {txn.currency or 'GEL'}; "
            f"platform refunded the customer but the restaurant's split of "
            f"{split.restaurant_amount} was not clawed back."
        ),
    )


def _apply_order_status(txn: BogTransaction) -> None:
    order: Order | None = txn.order
    if order is None:
        return
    if txn.is_successful and order.status == "pending_payment":
        order.status = "pending"  # Payment confirmed → enter the kitchen queue.
        order.save(update_fields=["status", "updated_at"])
        OrderStatusHistory.objects.create(
            order=order,
            from_status="pending_payment",
            to_status="pending",
            notes="Payment confirmed via BOG.",
        )
        # Mirror the successful charge into a Payment row so the standard admin /
        # reporting surfaces work for BOG-paid orders (receipt numbers, refund
        # accounting, customer payment history). Idempotent: skip if we already
        # created one for this BOG transaction.
        if not Payment.objects.filter(external_payment_id=txn.bog_order_id).exists():
            payment = Payment.objects.create(
                order=order,
                customer=order.customer,
                amount=txn.amount,
                total_amount=txn.amount,
                payment_method="card",
                status="pending",
                currency=txn.currency,
                external_payment_id=txn.bog_order_id,
            )
            payment.complete()  # flips to 'completed' + generates receipt_number

        # Credit platform-loyalty points. Helper is a no-op when the
        # restaurant hasn't opted in or the order was anonymous; safe
        # to call unconditionally here.
        try:
            from apps.loyalty.services import accrue_platform_points

            accrue_platform_points(order, source="bog")
        except Exception:  # pragma: no cover
            logger.exception("accrue_platform_points failed for order %s", order.id)

        # Wallet + referral side-effects. Both helpers are idempotent per
        # order, so webhook retries don't double-credit.
        try:
            from apps.referrals.services import credit_referral, spend_wallet

            if order.wallet_applied and order.wallet_applied > 0 and order.customer_id:
                spend_wallet(order.customer, order.wallet_applied, order)
            credit_referral(order)
        except Exception:  # pragma: no cover
            logger.exception("Referral / wallet side-effects failed for order %s", order.id)
    elif txn.status == BogTransaction.STATUS_REJECTED and order.status == "pending_payment":
        order.cancel(reason=f"BOG payment rejected ({txn.code or 'unknown'}): {txn.code_description}")
        OrderStatusHistory.objects.create(
            order=order,
            from_status="pending_payment",
            to_status="cancelled",
            notes=f"Payment rejected: {txn.reject_reason or txn.code_description}",
        )


def _apply_session_settle_status(txn: BogTransaction) -> None:
    """
    Record the aggregate settle in the Payment ledger. The covered orders'
    kitchen status was never gated on this flow (guest orders go straight to
    status=pending), so there's no order-level flip to do — the M2M link is
    what the close-guard / orders_summary check for "is this paid?".
    """
    if not txn.is_successful:
        return
    if Payment.objects.filter(external_payment_id=txn.bog_order_id).exists():
        return
    # Attach the ledger row to the first covered order so it shows up in
    # normal order-payment admin views. The bulk nature is captured by
    # notes + the M2M.
    anchor = txn.covered_orders.first()
    if anchor is None:
        return
    payment = Payment.objects.create(
        order=anchor,
        customer=anchor.customer,
        amount=txn.amount,
        total_amount=txn.amount,
        payment_method="card",
        status="pending",
        currency=txn.currency,
        external_payment_id=txn.bog_order_id,
        notes=f"Session settle — covers orders: {', '.join(o.order_number for o in txn.covered_orders.all())}",
    )
    payment.complete()


def _apply_reservation_status(txn: BogTransaction) -> None:
    reservation: Reservation | None = txn.reservation
    if reservation is None:
        return
    if txn.is_successful and reservation.status == "pending_payment":
        reservation.status = "pending"  # Await restaurant confirmation.
        reservation.save(update_fields=["status", "updated_at"])
    elif txn.status == BogTransaction.STATUS_REJECTED and reservation.status == "pending_payment":
        reservation.status = "cancelled"
        reservation.cancelled_at = timezone.now()
        reservation.cancellation_reason = f"BOG payment rejected ({txn.code or 'unknown'}): {txn.code_description}"
        reservation.save(update_fields=["status", "cancelled_at", "cancellation_reason", "updated_at"])


def _apply_add_card_status(txn: BogTransaction, payment_detail: dict[str, Any]) -> None:
    """Persist a PaymentMethod row once the tokenisation pre-auth succeeds."""
    if txn.status not in {
        BogTransaction.STATUS_COMPLETED,
        BogTransaction.STATUS_BLOCKED,
    }:
        return
    if not txn.initiated_by_id:
        logger.warning("Add-card txn %s has no initiated_by; skipping PaymentMethod creation", txn.id)
        return

    masked_pan = payment_detail.get("payer_identifier") or ""
    card_last4 = masked_pan[-4:] if masked_pan else ""
    card_brand = (payment_detail.get("card_type") or "").lower()
    card_exp = payment_detail.get("card_expiry_date") or ""  # "MM/YY"
    exp_month = exp_year = None
    if card_exp and "/" in card_exp:
        try:
            mm, yy = card_exp.split("/", 1)
            exp_month = int(mm.strip())
            exp_year = 2000 + int(yy.strip())
        except ValueError:
            exp_month = exp_year = None

    pm = PaymentMethod.objects.create(
        customer_id=txn.initiated_by_id,
        method_type="card",
        external_method_id=txn.bog_order_id,
        external_customer_id="",
        card_brand=card_brand,
        card_last4=card_last4,
        card_exp_month=exp_month,
        card_exp_year=exp_year,
        is_default=False,
        is_active=True,
    )
    txn.payment_method = pm
