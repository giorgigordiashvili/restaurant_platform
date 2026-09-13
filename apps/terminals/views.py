"""Dashboard API (POS), bank callbacks, the terminal bridge, and the guest's thank-you page."""

from __future__ import annotations

import json
import logging
import time

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
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired
from apps.orders.models import Order
from apps.payments.models import Payment
from apps.tables.models import TableSession
from apps.terminals import services
from apps.terminals.models import PaymentTerminal, TerminalTransaction
from apps.terminals.providers.base import Result
from apps.terminals.serializers import (
    BridgeJobSerializer,
    BridgeResultSerializer,
    ConfirmSerializer,
    DeclineSerializer,
    RefundSerializer,
    SendLinkSerializer,
    StartSaleSerializer,
    SummarySerializer,
    TerminalSerializer,
    TransactionSerializer,
)

logger = logging.getLogger(__name__)
TAG = "Dashboard - Terminals"
PERMS = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("terminals")]
MAX_WAIT = 20
POLL_STEP = 1.0


def _err(exc: services.TerminalServiceError, http=status.HTTP_409_CONFLICT):
    return Response({"success": False, "error": {"code": exc.code, "message": exc.message, **exc.extra}}, status=http)


@extend_schema(tags=[TAG], responses=SummarySerializer)
class SummaryView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request):
        return Response(SummarySerializer(services.summary(request.restaurant)).data)


@extend_schema(tags=[TAG], responses=TerminalSerializer(many=True))
class TerminalListView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request):
        return Response(TerminalSerializer(services.terminals_for(request.restaurant), many=True).data)


@extend_schema(tags=[TAG], parameters=[OpenApiParameter("status", str), OpenApiParameter("shift", str)])
class TransactionListView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request):
        qs = TerminalTransaction.objects.filter(restaurant=request.restaurant).select_related(
            "terminal", "order", "payment"
        )
        st = request.query_params.get("status")
        if st == "open":
            qs = qs.filter(status__in=TerminalTransaction.OPEN)
        elif st:
            qs = qs.filter(status__in=st.split(","))
        if request.query_params.get("order"):
            qs = qs.filter(order_id=request.query_params["order"])
        if request.query_params.get("session"):
            qs = qs.filter(session_id=request.query_params["session"])
        return Response(TransactionSerializer(qs[:100], many=True).data)

    @extend_schema(request=StartSaleSerializer, responses=TransactionSerializer)
    @require_restaurant
    def post(self, request):
        s = StartSaleSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        terminal = get_object_or_404(PaymentTerminal, pk=v["terminal_id"], restaurant=request.restaurant)
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
            tx = services.start_sale(
                request.restaurant,
                terminal,
                v["amount"],
                tip=v.get("tip_amount", 0),
                by=request.user,
                send_to=v.get("send_to", ""),
                **kwargs,
            )
        except services.TerminalServiceError as exc:
            return _err(exc, status.HTTP_400_BAD_REQUEST if exc.code in ("invalid_amount", "no_target") else 409)
        return Response(TransactionSerializer(tx).data, status=status.HTTP_201_CREATED)


class _TxView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "create")

    def _tx(self, request, tx_id) -> TerminalTransaction:
        return get_object_or_404(
            TerminalTransaction.objects.select_related("terminal", "restaurant", "order", "payment"),
            pk=tx_id,
            restaurant=request.restaurant,
        )


@extend_schema(tags=[TAG], responses=TransactionSerializer)
class TransactionDetailView(_TxView):
    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request, tx_id):
        tx = self._tx(request, tx_id)
        if tx.is_open and tx.terminal.is_link:
            tx = services.poll(tx)
        return Response(TransactionSerializer(tx).data)


@extend_schema(tags=[TAG], request=ConfirmSerializer, responses=TransactionSerializer)
class TransactionConfirmView(_TxView):
    @require_restaurant
    def post(self, request, tx_id):
        s = ConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            tx = services.confirm_manual(self._tx(request, tx_id), by=request.user, **s.validated_data)
        except services.TerminalServiceError as exc:
            return _err(exc)
        return Response(TransactionSerializer(tx).data)


@extend_schema(tags=[TAG], request=DeclineSerializer, responses=TransactionSerializer)
class TransactionDeclineView(_TxView):
    @require_restaurant
    def post(self, request, tx_id):
        s = DeclineSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            tx = services.decline_manual(self._tx(request, tx_id), by=request.user, reason=s.validated_data["reason"])
        except services.TerminalServiceError as exc:
            return _err(exc)
        return Response(TransactionSerializer(tx).data)


@extend_schema(tags=[TAG], request=None, responses=TransactionSerializer)
class TransactionCancelView(_TxView):
    @require_restaurant
    def post(self, request, tx_id):
        return Response(TransactionSerializer(services.cancel(self._tx(request, tx_id), by=request.user)).data)


@extend_schema(tags=[TAG], request=SendLinkSerializer, responses=TransactionSerializer)
class TransactionSendLinkView(_TxView):
    @require_restaurant
    def post(self, request, tx_id):
        s = SendLinkSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        tx = self._tx(request, tx_id)
        if not tx.pay_url:
            return Response({"success": False, "error": {"code": "no_link"}}, status=status.HTTP_409_CONFLICT)
        services.send_pay_link(tx, s.validated_data["to"], by=request.user)
        tx.refresh_from_db()
        return Response(TransactionSerializer(tx).data)


@extend_schema(tags=[TAG], request=RefundSerializer, responses=TransactionSerializer)
class PaymentTerminalRefundView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "delete")

    @require_restaurant
    def post(self, request, payment_id):
        s = RefundSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        payment = get_object_or_404(Payment, pk=payment_id, restaurant=request.restaurant)
        try:
            tx, _refund = services.start_refund(
                payment, s.validated_data["amount"], by=request.user, reason=s.validated_data["reason"]
            )
        except services.TerminalServiceError as exc:
            return _err(exc)
        except Exception as exc:  # LedgerError
            code = getattr(exc, "code", "refund_failed")
            return Response({"success": False, "error": {"code": code, "message": str(exc)}}, status=409)
        return Response(TransactionSerializer(tx).data, status=status.HTTP_201_CREATED)


# ── bank callbacks ─────────────────────────────────────────────────────────


class CallbackThrottle(AnonRateThrottle):
    scope = "terminal_callback"
    rate = "120/min"


@extend_schema(exclude=True)
class BogCallbackView(APIView):
    """BOG Payment Manager webhook for pay-by-link orders (same signature scheme as online checkout)."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [CallbackThrottle]

    def post(self, request):
        from apps.payments.bog.signatures import SignatureError, verify_signature
        from apps.terminals.providers.bog_link import result_from_receipt

        raw = request.body or b""
        signature = request.headers.get("Callback-Signature")
        try:
            if not verify_signature(raw, signature):
                return Response({"detail": "Bad signature."}, status=status.HTTP_401_UNAUTHORIZED)
        except SignatureError:
            return Response({"detail": "Signature verification unavailable."}, status=503)
        try:
            envelope = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return Response({"detail": "JSON expected."}, status=400)
        body = envelope.get("body") if isinstance(envelope, dict) else None
        if not isinstance(body, dict):
            return Response({"detail": "body missing."}, status=400)
        tx = (
            TerminalTransaction.objects.filter(
                terminal__provider="bog_link", external_id=str(body.get("order_id") or "")
            )
            .select_related("terminal", "restaurant")
            .first()
        )
        if tx is None:
            tx = (
                TerminalTransaction.objects.filter(pk__in=[str(body.get("external_order_id") or "")[:36]]).first()
                if body.get("external_order_id")
                else None
            )
        if tx is None:
            return Response({"detail": "Unknown transaction."}, status=404)
        services.apply_result(tx, result_from_receipt(body), source="callback")
        return Response({"status": "ok"})


@extend_schema(exclude=True)
class TbcCallbackView(APIView):
    """TBC posts {PaymentId}; the body is never trusted — the status is re-read from TBC."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [CallbackThrottle]

    def post(self, request):
        pay_id = str(request.data.get("PaymentId") or request.data.get("paymentId") or "")
        if not pay_id:
            return Response({"detail": "PaymentId missing."}, status=400)
        tx = (
            TerminalTransaction.objects.filter(terminal__provider="tbc_tpay", external_id=pay_id)
            .select_related("terminal", "restaurant")
            .first()
        )
        if tx is None:
            return Response({"detail": "Unknown transaction."}, status=404)
        services.poll(tx)
        return Response({"status": "ok"})


# ── the terminal bridge ────────────────────────────────────────────────────


def _terminal(request):
    key = request.headers.get("X-Bridge-Key") or request.query_params.get("key")
    if not key:
        return None
    return (
        PaymentTerminal.objects.select_related("restaurant")
        .filter(bridge_key=key, is_active=True, provider="ecr_bridge")
        .first()
    )


class BridgeView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        request.terminal = _terminal(request)

    def _unauthorised(self):
        return Response({"detail": "Unknown or inactive bridge key."}, status=status.HTTP_401_UNAUTHORIZED)


@extend_schema(tags=["Terminal bridge"], responses={200: dict})
class BridgePingView(BridgeView):
    def get(self, request):
        if request.terminal is None:
            return self._unauthorised()
        services.heartbeat(request.terminal)
        t = request.terminal
        return Response(
            {"terminal": t.name, "protocol": t.ecr_protocol, "device": t.connection, "restaurant": t.restaurant.name}
        )


@extend_schema(
    tags=["Terminal bridge"],
    parameters=[OpenApiParameter("wait", int, description=f"Long-poll seconds (0..{MAX_WAIT})")],
    responses={200: BridgeJobSerializer, 204: None},
)
class BridgeNextJobView(BridgeView):
    def get(self, request):
        if request.terminal is None:
            return self._unauthorised()
        try:
            wait = min(max(int(request.query_params.get("wait", 0)), 0), MAX_WAIT)
        except ValueError:
            wait = 0
        if not services.enabled(request.terminal.restaurant):
            return Response(status=status.HTTP_204_NO_CONTENT)
        deadline = time.monotonic() + wait
        while True:
            tx = services.claim_next(request.terminal)
            if tx is not None:
                return Response(services.bridge_job(tx))
            if time.monotonic() >= deadline:
                return Response(status=status.HTTP_204_NO_CONTENT)
            time.sleep(POLL_STEP)


@extend_schema(tags=["Terminal bridge"], request=BridgeResultSerializer, responses={200: dict})
class BridgeResultView(BridgeView):
    def post(self, request, tx_id):
        if request.terminal is None:
            return self._unauthorised()
        tx = (
            TerminalTransaction.objects.filter(pk=tx_id, terminal=request.terminal)
            .select_related("terminal", "restaurant")
            .first()
        )
        if tx is None:
            return Response({"detail": "Unknown job."}, status=404)
        s = BridgeResultSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        result = Result(
            status=v["status"],
            auth_code=v["auth_code"],
            card_mask=v["card_mask"],
            rrn=v["rrn"],
            error=v["error"],
            raw=v.get("raw") or {},
        )
        if tx.kind == "refund":
            tx = services.complete_bridge_refund(tx, result)
        else:
            tx = services.apply_result(tx, result, source="bridge")
        return Response({"status": tx.status})


# ── guest thank-you page ───────────────────────────────────────────────────


@extend_schema(exclude=True)
class PayPageView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [CallbackThrottle]

    def get(self, request, tx_id):
        tx = TerminalTransaction.objects.filter(pk=tx_id).select_related("terminal", "restaurant").first()
        if tx is None:
            body = f"<p>{_('This link is not valid.')}</p>"
            code = 404
        else:
            if tx.is_open and tx.terminal.is_link:
                tx = services.poll(tx)
            if tx.status == "approved":
                body = f"<h2>✅ {_('Paid')}</h2><p>{tx.restaurant.name} · {tx.total} {tx.currency}</p><p>{_('Thank you!')}</p>"
            elif tx.is_open:
                pay = (
                    f"<p><a href=\"{tx.pay_url}\"><button type=\"button\">{_('Pay now')}</button></a></p>"
                    if tx.pay_url
                    else f"<p>{_('Follow the terminal.')}</p>"
                )
                body = f"<h2>{tx.restaurant.name}</h2><p>{tx.total} {tx.currency}</p>{pay}"
            else:
                body = f"<h2>{_('Payment not completed')}</h2><p>{tx.restaurant.name} · {tx.get_status_display()}</p><p>{_('Ask the staff for a new link.')}</p>"
            code = 200
        html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>AiMenu</title><style>body{{font-family:system-ui,sans-serif;max-width:480px;margin:60px auto;padding:0 20px;color:#111;text-align:center}}
button{{padding:12px 22px;border-radius:12px;border:0;background:#EC003F;color:#fff;font-size:16px}}</style></head><body>{body}</body></html>"""
        return HttpResponse(html, status=code)
