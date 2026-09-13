"""Courier-platform webhooks: Wolt Drive (JWT body) and Glovo On-Demand (HMAC header)."""

from __future__ import annotations

import json
import logging

from django.db import transaction

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.delivery.courier.base import CourierError
from apps.ordering import dispatch
from apps.ordering.models import Delivery, DeliveryEvent

logger = logging.getLogger(__name__)


def _find(provider: str, external_id: str, order_number: str = "") -> Delivery | None:
    qs = Delivery.objects.select_related("order__restaurant", "restaurant").filter(provider=provider)
    d = qs.filter(external_id=external_id).first() if external_id else None
    if d is None and order_number:
        d = qs.filter(order__order_number=order_number).order_by("-created_at").first()
    return d


def _process(delivery: Delivery, event_id: str, kind: str, payload: dict, result) -> Response:
    from django.utils import timezone

    with transaction.atomic():
        event, created = DeliveryEvent.objects.select_for_update().get_or_create(
            delivery=delivery, event_id=event_id, defaults={"kind": kind, "payload": payload}
        )
        if not created and event.processed_at:
            return Response({"status": "already_processed"})
        dispatch.apply_status(delivery, result, source="webhook")
        event.processed_at = timezone.now()
        event.save(update_fields=["processed_at"])
    return Response({"status": "accepted"})


@extend_schema(exclude=True)
class WoltDriveWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        from apps.delivery.courier.wolt_drive import status_from_payload, verify_webhook

        body = request.body or b""
        # The order reference is inside the JWT; peek without verifying to find the link, then verify.
        try:
            import jwt

            text = body.decode("utf-8", "replace").strip()
            token = json.loads(text).get("token", "") if text.startswith("{") else text
            claims = jwt.decode(token, options={"verify_signature": False}) if token else {}
        except Exception:  # noqa: BLE001
            claims = {}
        details = claims.get("details") or claims.get("delivery") or claims
        ref = str(details.get("wolt_order_reference_id") or details.get("id") or claims.get("id") or "")
        merchant_ref = str(
            details.get("merchant_order_reference_id") or claims.get("merchant_order_reference_id") or ""
        )
        delivery = _find("wolt_drive", ref, merchant_ref)
        if delivery is None:
            return Response({"detail": "Unknown delivery."}, status=status.HTTP_404_NOT_FOUND)
        link = dispatch.registry.link_for(delivery.restaurant, "wolt_drive")
        try:
            claims = verify_webhook(link, body)
        except CourierError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        details = claims.get("details") or claims.get("delivery") or claims
        event_type = str(claims.get("type") or claims.get("event") or "")
        event_id = str(claims.get("id") or claims.get("jti") or f"{event_type}:{claims.get('timestamp', '')}")
        result = status_from_payload(details if isinstance(details, dict) else {}, event_type=event_type)
        return _process(delivery, f"wolt_drive:{event_id}", event_type, claims, result)


@extend_schema(exclude=True)
class GlovoOdrCallbackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        from apps.delivery.courier.glovo_odr import signature_valid, status_from_payload

        body = request.body or b""
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return Response({"detail": "JSON expected."}, status=status.HTTP_400_BAD_REQUEST)
        ref = str(payload.get("trackingNumber") or payload.get("orderId") or payload.get("id") or "")
        client_ref = str(payload.get("clientOrderId") or payload.get("client_order_id") or "")
        delivery = _find("glovo_odr", ref, client_ref)
        if delivery is None:
            return Response({"detail": "Unknown delivery."}, status=status.HTTP_404_NOT_FOUND)
        link = dispatch.registry.link_for(delivery.restaurant, "glovo_odr")
        if not signature_valid(link, body, request.headers.get("X-Signature-SHA256", "")):
            return Response({"detail": "Bad signature."}, status=status.HTTP_401_UNAUTHORIZED)
        state = str(payload.get("state") or payload.get("status") or "")
        event_id = str(payload.get("eventId") or payload.get("id") or f"{ref}:{state}:{payload.get('updatedAt', '')}")
        return _process(delivery, f"glovo_odr:{event_id}", state, payload, status_from_payload(payload))
