"""
Wolt order webhook. Wolt only tells us *that* an order changed; the payload
is fetched by a Celery task (no network calls inline). The body is
HMAC-signed with the secret we handed Wolt (the link's webhook token).
"""

from __future__ import annotations

import json
import logging

from django.db import transaction

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.delivery import services
from apps.delivery.config import wolt_signature_valid
from apps.delivery.models import DeliveryPlatformEvent
from apps.delivery.wolt.orders import parse_notification

logger = logging.getLogger(__name__)


@extend_schema(exclude=True)
class WoltOrderWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        body = request.body or b""
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return Response({"detail": "JSON expected."}, status=status.HTTP_400_BAD_REQUEST)
        note = parse_notification(payload)
        if not note.order_id:
            return Response({"detail": "order.id missing."}, status=status.HTTP_400_BAD_REQUEST)
        link = services.link_for_store("wolt", note.venue_id)
        if link is None:
            return Response({"detail": "Unknown venue."}, status=status.HTTP_404_NOT_FOUND)
        if not wolt_signature_valid(link, body, request.headers.get("WOLT-SIGNATURE", "")):
            return Response({"detail": "Bad signature."}, status=status.HTTP_401_UNAUTHORIZED)
        if not (link.is_enabled and services.enabled(link.restaurant)):
            return Response({"detail": "Integration disabled."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        with transaction.atomic():
            event, created = DeliveryPlatformEvent.objects.select_for_update().get_or_create(
                link=link,
                event_id=f"wolt:{note.order_id}:{note.status or 'UNKNOWN'}",
                defaults={"kind": "order_status", "payload": payload},
            )
            if not created and event.processed_at:
                return Response({"status": "already_processed"})
            services.handle_wolt_notification(link, note, event=event)
        return Response({"status": "accepted"})
