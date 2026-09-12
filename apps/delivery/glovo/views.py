"""
Glovo webhooks and the public menu feed. No auth classes: Glovo presents the
integration's Authorization value, compared in constant time; the menu feed
is guarded by an unguessable token in the URL. Webhooks do no network calls
inline -- everything outbound is Celery.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import transaction
from django.utils.crypto import constant_time_compare

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.delivery import services
from apps.delivery.config import webhook_token_matches
from apps.delivery.glovo.menu import build_menu
from apps.delivery.glovo.orders import parse_order
from apps.delivery.models import DeliveryPlatformEvent, RestaurantDeliveryPlatform

logger = logging.getLogger(__name__)


class MenuFeedThrottle(AnonRateThrottle):
    rate = "60/minute"


class _GlovoWebhook(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def _resolve(self, request, payload):
        if not isinstance(payload, dict):
            return None, Response({"detail": "JSON object expected."}, status=status.HTTP_400_BAD_REQUEST)
        link = services.link_for_store("glovo", payload.get("store_id"))
        if link is None:
            return None, Response({"detail": "Unknown store."}, status=status.HTTP_404_NOT_FOUND)
        if not webhook_token_matches(link, request.headers.get("Authorization", "")):
            return None, Response({"detail": "Bad token."}, status=status.HTTP_401_UNAUTHORIZED)
        if not (link.is_enabled and services.enabled(link.restaurant)):
            # 503: Glovo retries later instead of silently losing the order.
            return None, Response({"detail": "Integration disabled."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return link, None


@extend_schema(exclude=True)
class GlovoOrderWebhookView(_GlovoWebhook):
    def post(self, request):
        link, error = self._resolve(request, request.data)
        if error is not None:
            return error
        parsed = parse_order(request.data)
        if not parsed.order_id:
            return Response({"detail": "order_id missing."}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            event, created = DeliveryPlatformEvent.objects.select_for_update().get_or_create(
                link=link,
                event_id=f"glovo:{parsed.order_id}:order_created",
                defaults={"kind": "order_created", "payload": request.data},
            )
            if not created and event.processed_at and event.order_id:
                return Response({"order_id": str(event.order_id)})
            order = services.create_platform_order(link, parsed, event=event)
        return Response({"order_id": str(order.pk)})


@extend_schema(exclude=True)
class GlovoCancelWebhookView(_GlovoWebhook):
    def post(self, request, order_id):
        payload = dict(request.data) if isinstance(request.data, dict) else {}
        payload.setdefault("order_id", order_id)
        link, error = self._resolve(request, payload)
        if error is not None:
            return error
        with transaction.atomic():
            event, created = DeliveryPlatformEvent.objects.select_for_update().get_or_create(
                link=link,
                event_id=f"glovo:{order_id}:order_cancelled",
                defaults={"kind": "order_cancelled", "payload": payload},
            )
            if not created and event.processed_at:
                return Response({"status": "already_processed"})
            order = services.cancel_platform_order(
                link, str(order_id), reason=str(payload.get("cancel_reason") or ""), event=event
            )
            if order is None:
                event.processed_at = None
                event.error = "unknown order"
                event.save(update_fields=["error", "updated_at"])
        return Response({"status": "cancelled" if order else "unknown_order"})


@extend_schema(exclude=True)
class GlovoMenuFeedView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [MenuFeedThrottle]

    def get(self, request, link_id, token):
        link = (
            RestaurantDeliveryPlatform.objects.select_related("restaurant").filter(pk=link_id, platform="glovo").first()
        )
        if link is None or not link.menu_token or not constant_time_compare(token, link.menu_token):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        key = f"glovo-menu:{link.pk}"
        menu = cache.get(key)
        if menu is None:
            menu = build_menu(link)
            cache.set(key, menu, 60)
        return Response(menu)
