"""
Views for Flitt (pay.flitt.com) Payment Manager integration.

Flow:

1. Customer clicks "Pay with Flitt" on the checkout page.
2. Frontend → ``POST /api/v1/payments/flitt/initiate/`` with the same
   ``{target, ..._payload, return_url}`` shape the BOG endpoint accepts.
3. Backend creates the Order / Reservation (via the shared
   ``initiate_helpers`` module), POSTs Flitt's ``/api/checkout/url``,
   persists a :class:`FlittTransaction`, and returns the hosted-page
   ``redirect_url``.
4. Customer pays on Flitt's hosted page. Flitt fires a server-callback
   to ``FlittWebhookView`` with the terminal status.
5. On ``approved``, the webhook handler triggers a second call to
   ``/api/settlement`` that fans the amount out to the platform sub-merchant
   (5 %) + the restaurant sub-merchant (95 %). This is the "split".

All HTTP communication with Flitt is HMAC-SHA1 signed (see ``signatures.py``).
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

from apps.orders.models import Order, OrderStatusHistory
from apps.payments.initiate_helpers import (
    OrderInitiateResult,
    ReservationInitiateResult,
    create_pending_order,
    create_pending_reservation,
)
from apps.payments.models import FlittTransaction
from apps.payments.splits import compute_split

from .client import FlittClient, FlittClientError, get_client
from .serializers import FlittInitiatePaymentSerializer
from .signatures import verify

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _assert_configured() -> Response | None:
    """Early 503 if Flitt settings aren't populated — matches BOG's pattern."""
    missing: list[str] = []
    if not getattr(settings, "FLITT_MERCHANT_ID", ""):
        missing.append("FLITT_MERCHANT_ID")
    if not getattr(settings, "FLITT_SECRET_KEY", ""):
        missing.append("FLITT_SECRET_KEY")
    if missing:
        return Response(
            {
                "success": False,
                "error": {
                    "code": "flitt_not_configured",
                    "message": f"Flitt is not configured: missing {', '.join(missing)}.",
                },
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return None


def _callback_url(request: Request) -> str:
    """Absolute URL Flitt will POST server-callbacks to."""
    override = getattr(settings, "FLITT_WEBHOOK_URL", "") or ""
    if override:
        return override
    return request.build_absolute_uri("/api/v1/payments/flitt/webhook/")


def _redirect_urls(return_url: str, external_order_id: str) -> dict[str, str]:
    """Same redirect_urls shape BOG uses, reused for symmetry on the return page."""
    from urllib.parse import urlencode, urlparse, urlunparse

    parsed = urlparse(return_url)

    def _with(status_value: str) -> str:
        extra = urlencode({"ref": external_order_id, "status": status_value, "provider": "flitt"})
        joined = f"{parsed.query}&{extra}" if parsed.query else extra
        return urlunparse(parsed._replace(query=joined))

    return {"success": _with("ok"), "fail": _with("fail")}


def _client_ip(request: Request) -> str:
    """Extract the public client IP, honouring common proxy headers."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    return forwarded or request.META.get("REMOTE_ADDR", "")


# ── Initiate ────────────────────────────────────────────────────────────────


@extend_schema(tags=["Payments: Flitt"])
class FlittInitiatePaymentView(APIView):
    """Build an Order / Reservation and hand the customer a Flitt checkout URL."""

    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        guard = _assert_configured()
        if guard is not None:
            return guard

        serializer = FlittInitiatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target = data["target"]
        return_url = data["return_url"]

        try:
            if target == FlittInitiatePaymentSerializer.TARGET_ORDER:
                return self._initiate_order(request, data["order_payload"], return_url)
            if target == FlittInitiatePaymentSerializer.TARGET_RESERVATION:
                return self._initiate_reservation(request, data["reservation_payload"], return_url)
            # Session settle via Flitt is out of scope for v1 — BOG handles
            # the pay-QR flow for now. Return a clear 400 so the frontend
            # can fall back to BOG when the customer picked Flitt and the
            # target is "session".
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "flitt_target_unsupported",
                        "message": "Flitt currently supports order + reservation payments. Use BOG for table-QR settle.",
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ValueError as exc:
            return Response(
                {"success": False, "error": {"message": str(exc)}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except FlittClientError as exc:
            logger.exception(
                "Flitt upstream call failed during initiate (%s) status=%s payload=%r request=%r",
                target,
                getattr(exc, "status_code", None),
                getattr(exc, "payload", None),
                data,
            )
            return Response(
                {
                    "success": False,
                    "error": {
                        "code": "flitt_upstream",
                        "message": "Payment gateway is temporarily unavailable. Please try again.",
                        "detail": str(exc),
                    },
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except ImproperlyConfigured as exc:
            return Response(
                {"success": False, "error": {"message": str(exc)}},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    @transaction.atomic
    def _initiate_order(self, request: Request, payload: dict[str, Any], return_url: str) -> Response:
        result: OrderInitiateResult = create_pending_order(request, payload)
        if not result.restaurant.accepts_flitt_payments or not result.restaurant.flitt_sub_merchant_id:
            raise ValueError("This restaurant does not accept Flitt payments.")

        flitt_order_id = str(uuid.uuid4())
        body = self._build_checkout_body(
            flitt_order_id=flitt_order_id,
            amount=result.amount,
            description=f"Order {result.order.order_number}",
            return_url=return_url,
            callback_url=_callback_url(request),
            external_order_id=result.order.order_number,
        )

        response = get_client().create_checkout(body)
        inner = response.get("response") or {}
        checkout_url = inner.get("checkout_url")
        flitt_payment_id = str(inner.get("payment_id") or "")
        if not checkout_url:
            raise FlittClientError("Flitt response missing checkout_url", payload=response)

        txn = FlittTransaction.objects.create(
            flitt_order_id=flitt_order_id,
            flitt_payment_id=flitt_payment_id,
            flow_type=FlittTransaction.FLOW_ORDER,
            order=result.order,
            initiated_by=request.user if request.user.is_authenticated else None,
            amount=result.amount,
            currency="GEL",
            status=FlittTransaction.STATUS_PROCESSING,
            checkout_url=checkout_url,
            return_url=return_url,
            callback_url=body["server_callback_url"],
            request_payload=body,
            response_payload=response,
        )
        return Response(
            {
                "success": True,
                "data": {
                    "provider": "flitt",
                    "flitt_order_id": txn.flitt_order_id,
                    "flitt_payment_id": txn.flitt_payment_id,
                    "redirect_url": checkout_url,
                    "order_number": result.order.order_number,
                    "amount": str(result.amount),
                    "currency": "GEL",
                },
            }
        )

    @transaction.atomic
    def _initiate_reservation(self, request: Request, payload: dict[str, Any], return_url: str) -> Response:
        result: ReservationInitiateResult = create_pending_reservation(request, payload)
        if not result.restaurant.accepts_flitt_payments or not result.restaurant.flitt_sub_merchant_id:
            raise ValueError("This restaurant does not accept Flitt payments.")

        flitt_order_id = str(uuid.uuid4())
        body = self._build_checkout_body(
            flitt_order_id=flitt_order_id,
            amount=result.amount,
            description=f"Reservation {result.reservation.confirmation_code}",
            return_url=return_url,
            callback_url=_callback_url(request),
            external_order_id=result.reservation.confirmation_code,
        )

        response = get_client().create_checkout(body)
        inner = response.get("response") or {}
        checkout_url = inner.get("checkout_url")
        flitt_payment_id = str(inner.get("payment_id") or "")
        if not checkout_url:
            raise FlittClientError("Flitt response missing checkout_url", payload=response)

        FlittTransaction.objects.create(
            flitt_order_id=flitt_order_id,
            flitt_payment_id=flitt_payment_id,
            flow_type=FlittTransaction.FLOW_RESERVATION,
            reservation=result.reservation,
            order=result.pre_order,
            initiated_by=request.user if request.user.is_authenticated else None,
            amount=result.amount,
            currency="GEL",
            status=FlittTransaction.STATUS_PROCESSING,
            checkout_url=checkout_url,
            return_url=return_url,
            callback_url=body["server_callback_url"],
            request_payload=body,
            response_payload=response,
        )
        return Response(
            {
                "success": True,
                "data": {
                    "provider": "flitt",
                    "flitt_order_id": flitt_order_id,
                    "flitt_payment_id": flitt_payment_id,
                    "redirect_url": checkout_url,
                    "reservation_confirmation_code": result.reservation.confirmation_code,
                    "amount": str(result.amount),
                    "currency": "GEL",
                },
            }
        )

    def _build_checkout_body(
        self,
        *,
        flitt_order_id: str,
        amount: Decimal,
        description: str,
        return_url: str,
        callback_url: str,
        external_order_id: str,
    ) -> dict[str, Any]:
        return {
            "order_id": flitt_order_id,
            "order_desc": description,
            "amount": int((Decimal(str(amount)) * 100).quantize(Decimal("1"))),
            "currency": "GEL",
            "server_callback_url": callback_url,
            "response_url": _redirect_urls(return_url, external_order_id)["success"],
        }


# ── Webhook ─────────────────────────────────────────────────────────────────


@extend_schema(tags=["Payments: Flitt"])
class FlittWebhookView(APIView):
    """
    Receives Flitt's server-callback POSTs. IP + HMAC-SHA1 verified before
    any state mutation; unknown ``order_id`` returns 200 (to avoid Flitt
    retry storms on truly foreign payloads).
    """

    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        allowed_ips = set(getattr(settings, "FLITT_ALLOWED_WEBHOOK_IPS", []) or [])
        if allowed_ips and _client_ip(request) not in allowed_ips:
            logger.warning("Rejecting Flitt webhook from unlisted IP %s", _client_ip(request))
            return Response(status=status.HTTP_403_FORBIDDEN)

        body = request.data
        if not isinstance(body, dict):
            return Response(status=status.HTTP_400_BAD_REQUEST)

        received_signature = body.get("signature") or ""
        if not verify(body, received_signature, settings.FLITT_SECRET_KEY):
            logger.warning("Flitt webhook signature verification failed")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        flitt_order_id = body.get("order_id") or ""
        if not flitt_order_id:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            try:
                txn = FlittTransaction.objects.select_for_update().get(flitt_order_id=flitt_order_id)
            except FlittTransaction.DoesNotExist:
                # Ack with 200 so Flitt doesn't retry what we'll never handle.
                return Response(status=status.HTTP_200_OK)

            txn.status = self._map_status(body.get("order_status") or "")
            txn.last_webhook_payload = body
            txn.last_webhook_at = timezone.now()
            if body.get("payment_id") and not txn.flitt_payment_id:
                txn.flitt_payment_id = str(body["payment_id"])
            txn.save(
                update_fields=[
                    "status",
                    "last_webhook_payload",
                    "last_webhook_at",
                    "flitt_payment_id",
                    "updated_at",
                ]
            )
            _fan_out_flitt_status(txn)
        return Response(status=status.HTTP_200_OK)

    @staticmethod
    def _map_status(raw: str) -> str:
        """
        Translate Flitt's ``order_status`` into our own status choices.
        Flitt strings (lowercase): ``approved``, ``declined``, ``expired``,
        ``reversed``, ``processing``, ``created``. Anything unexpected falls
        back to ``processing`` so we don't flip a terminal state on malformed
        input.
        """
        mapping = {
            "approved": FlittTransaction.STATUS_APPROVED,
            "declined": FlittTransaction.STATUS_DECLINED,
            "expired": FlittTransaction.STATUS_EXPIRED,
            "reversed": FlittTransaction.STATUS_REVERSED,
            "partially_reversed": FlittTransaction.STATUS_REVERSED_PARTIALLY,
            "processing": FlittTransaction.STATUS_PROCESSING,
            "created": FlittTransaction.STATUS_CREATED,
        }
        return mapping.get(raw.lower(), FlittTransaction.STATUS_PROCESSING)


# ── Status polling ──────────────────────────────────────────────────────────


@extend_schema(tags=["Payments: Flitt"])
class FlittStatusView(APIView):
    """Read-only status poll — the frontend return page uses this as a fallback."""

    permission_classes = [AllowAny]

    def get(self, request: Request, flitt_order_id: str) -> Response:
        try:
            txn = FlittTransaction.objects.get(flitt_order_id=flitt_order_id)
        except FlittTransaction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "success": True,
                "data": {
                    "status": txn.status,
                    "settlement_status": txn.settlement_status,
                    "order_number": txn.order.order_number if txn.order else None,
                    "reservation_id": (str(txn.reservation.id) if txn.reservation else None),
                    "session_id": str(txn.session.id) if txn.session else None,
                    "flow_type": txn.flow_type,
                },
            }
        )


# ── Fan-out + settlement ───────────────────────────────────────────────────


def _fan_out_flitt_status(txn: FlittTransaction) -> None:
    """
    Post-status hook: flip Order / Reservation state when Flitt approves,
    and kick off the split settlement call in the same transaction.
    """
    if txn.status == FlittTransaction.STATUS_APPROVED:
        _apply_success_side_effects(txn)
        _attempt_settlement(txn)


def _apply_success_side_effects(txn: FlittTransaction) -> None:
    """Promote the linked Order / Reservation to the next status."""
    if txn.order_id and txn.order:
        order: Order = txn.order
        if order.status == "pending_payment":
            order.status = "pending"
            order.save(update_fields=["status", "updated_at"])
            OrderStatusHistory.objects.create(
                order=order,
                from_status="pending_payment",
                to_status="pending",
                notes="Payment confirmed via Flitt.",
            )
        # Wallet + referral side-effects. Idempotent per (user, order, kind);
        # safe under Flitt's webhook retry policy.
        try:
            from apps.referrals.services import credit_referral, spend_wallet

            if order.wallet_applied and order.wallet_applied > 0 and order.customer_id:
                spend_wallet(order.customer, order.wallet_applied, order)
            credit_referral(order)
        except Exception:  # pragma: no cover
            logger.exception("Referral / wallet side-effects failed for order %s", order.id)
    if txn.reservation_id and txn.reservation:
        reservation = txn.reservation
        if reservation.status == "pending_payment":
            reservation.status = "confirmed"
            reservation.save(update_fields=["status", "updated_at"])


def _attempt_settlement(txn: FlittTransaction) -> None:
    """
    Call ``/api/settlement`` to fan the approved amount into two sub-merchant
    legs (platform 5 %, restaurant 95 %). On any failure we mark the txn for
    later retry — Celery (apps.payments.tasks.retry_failed_flitt_settlements)
    will re-run.
    """
    restaurant = txn.restaurant_for_txn
    if restaurant is None:
        logger.warning("Flitt txn %s has no restaurant; cannot settle.", txn.flitt_order_id)
        return
    platform_sub = getattr(settings, "FLITT_PLATFORM_SUB_MERCHANT_ID", "")
    if not platform_sub or not restaurant.flitt_sub_merchant_id:
        logger.warning(
            "Flitt settlement skipped for %s — missing sub-merchant ids (platform=%r restaurant=%r).",
            txn.flitt_order_id,
            platform_sub,
            restaurant.flitt_sub_merchant_id,
        )
        txn.settlement_status = FlittTransaction.SETTLEMENT_ERROR
        txn.settlement_error = "Missing sub-merchant ids"
        txn.save(update_fields=["settlement_status", "settlement_error", "updated_at"])
        return

    split = compute_split(txn.amount, restaurant)
    amount_minor = int((Decimal(str(txn.amount)) * 100).quantize(Decimal("1")))
    platform_minor = int((split.platform_amount * 100).quantize(Decimal("1")))
    restaurant_minor = amount_minor - platform_minor

    settlement_body = {
        "order_type": "settlement",
        "order_id": f"settlement_{txn.flitt_order_id}",
        "operation_id": txn.flitt_order_id,
        "amount": amount_minor,
        "currency": txn.currency or "GEL",
        "merchant_id": settings.FLITT_MERCHANT_ID,
        "server_callback_url": txn.callback_url,
        "response_url": txn.return_url,
        "order_desc": f"Split for {txn.flitt_order_id}",
        "receiver": [
            {
                "type": "merchant",
                "requisites": {
                    "merchant_id": platform_sub,
                    "amount": platform_minor,
                },
            },
            {
                "type": "merchant",
                "requisites": {
                    "merchant_id": restaurant.flitt_sub_merchant_id,
                    "amount": restaurant_minor,
                },
            },
        ],
    }

    try:
        client: FlittClient = get_client()
        response = client.create_settlement(settlement_body)
    except FlittClientError as exc:
        logger.exception("Flitt settlement failed for %s: %s", txn.flitt_order_id, exc)
        txn.settlement_status = FlittTransaction.SETTLEMENT_PENDING
        txn.settlement_error = str(exc)[:500]
        txn.split_snapshot = split.to_dict()
        txn.save(
            update_fields=[
                "settlement_status",
                "settlement_error",
                "split_snapshot",
                "updated_at",
            ]
        )
        return

    inner = response.get("response") if isinstance(response, dict) else {}
    txn.settlement_id = str((inner or {}).get("order_id") or "")
    txn.settlement_status = FlittTransaction.SETTLEMENT_SETTLED
    txn.settlement_error = ""
    txn.settled_at = timezone.now()
    txn.split_snapshot = split.to_dict()
    txn.save(
        update_fields=[
            "settlement_id",
            "settlement_status",
            "settlement_error",
            "settled_at",
            "split_snapshot",
            "updated_at",
        ]
    )


# ── Refund (dashboard) ──────────────────────────────────────────────────────


@extend_schema(tags=["Payments: Flitt"])
class FlittRefundView(APIView):
    """Platform / restaurant staff trigger a Flitt reversal."""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, id) -> Response:
        try:
            txn = FlittTransaction.objects.get(id=id)
        except FlittTransaction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if txn.status != FlittTransaction.STATUS_APPROVED:
            return Response(
                {"success": False, "error": {"message": "Only approved transactions can be reversed."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        split = txn.split_snapshot or {}
        platform_sub = getattr(settings, "FLITT_PLATFORM_SUB_MERCHANT_ID", "")
        restaurant = txn.restaurant_for_txn
        if not split or not restaurant or not platform_sub:
            return Response(
                {"success": False, "error": {"message": "Cannot build proportional refund — split snapshot missing."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        amount_minor = int((Decimal(str(txn.amount)) * 100).quantize(Decimal("1")))
        platform_minor = int((Decimal(str(split.get("platform_amount", "0"))) * 100).quantize(Decimal("1")))
        restaurant_minor = amount_minor - platform_minor

        try:
            response = get_client().reverse_order(
                txn.flitt_order_id,
                {
                    "amount": amount_minor,
                    "currency": txn.currency or "GEL",
                    "receiver": [
                        {"type": "merchant", "requisites": {"merchant_id": platform_sub, "amount": platform_minor}},
                        {
                            "type": "merchant",
                            "requisites": {
                                "merchant_id": restaurant.flitt_sub_merchant_id,
                                "amount": restaurant_minor,
                            },
                        },
                    ],
                },
            )
        except FlittClientError as exc:
            return Response(
                {"success": False, "error": {"message": str(exc)}},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        txn.status = FlittTransaction.STATUS_REVERSED
        txn.save(update_fields=["status", "updated_at"])

        # Reverse referral credit + return wallet spend on the underlying order.
        if txn.order_id and txn.order:
            try:
                from apps.referrals.services import clawback_referral, refund_wallet_spend

                clawback_referral(txn.order)
                refund_wallet_spend(txn.order)
            except Exception:  # pragma: no cover
                logger.exception("Referral clawback / wallet refund failed for order %s", txn.order_id)

        return Response({"success": True, "data": response})
