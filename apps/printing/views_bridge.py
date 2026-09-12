"""
The print bridge API. No session, no JWT, no throttling: the bridge
identifies its printer with ``X-Bridge-Key``. ``jobs/next/`` long-polls so
a ticket appears on paper within a second of the kitchen accepting it.
"""

import base64
import time

from django.conf import settings

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiParameter, extend_schema

from . import services
from .models import Printer, PrintJob
from .serializers import BridgeFailSerializer, BridgeJobSerializer

MAX_WAIT = int(getattr(settings, "PRINT_BRIDGE_LONG_POLL_SECONDS", 15))
POLL_STEP = 1.0


def _printer(request):
    key = request.headers.get("X-Bridge-Key") or request.query_params.get("key")
    if not key:
        return None
    return (
        Printer.objects.select_related("restaurant").filter(bridge_key=key, is_active=True, connection="bridge").first()
    )


class BridgeView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        request.printer = _printer(request)

    def _unauthorised(self):
        return Response({"detail": "Unknown or inactive bridge key."}, status=status.HTTP_401_UNAUTHORIZED)


@extend_schema(tags=["Print bridge"], responses={200: dict})
class BridgePingView(BridgeView):
    def get(self, request):
        if request.printer is None:
            return self._unauthorised()
        services.heartbeat(request.printer)
        p = request.printer
        return Response(
            {"printer": p.name, "kind": p.kind, "paper": p.paper, "copies": p.copies, "restaurant": p.restaurant.name}
        )


@extend_schema(
    tags=["Print bridge"],
    parameters=[OpenApiParameter("wait", int, description=f"Long-poll seconds (0..{MAX_WAIT})")],
    responses={200: BridgeJobSerializer, 204: None},
)
class BridgeNextJobView(BridgeView):
    def get(self, request):
        printer = request.printer
        if printer is None:
            return self._unauthorised()
        try:
            wait = min(max(int(request.query_params.get("wait", 0)), 0), MAX_WAIT)
        except ValueError:
            wait = 0
        if not printer.restaurant.printing_enabled:
            return Response(status=status.HTTP_204_NO_CONTENT)
        deadline = time.monotonic() + wait
        while True:
            job = services.claim_next(printer)
            if job is not None:
                return Response(
                    {
                        "id": str(job.pk),
                        "kind": job.kind,
                        "title": job.title,
                        "copies": printer.copies,
                        "escpos_b64": base64.b64encode(bytes(job.escpos)).decode("ascii"),
                    }
                )
            if time.monotonic() >= deadline:
                return Response(status=status.HTTP_204_NO_CONTENT)
            time.sleep(POLL_STEP)


class _JobAck(BridgeView):
    def _job(self, request, id):
        if request.printer is None:
            return None
        return PrintJob.objects.filter(pk=id, printer=request.printer).first()


@extend_schema(tags=["Print bridge"], request=None, responses={200: dict})
class BridgeJobDoneView(_JobAck):
    def post(self, request, id):
        if request.printer is None:
            return self._unauthorised()
        job = self._job(request, id)
        if job is None:
            return Response({"detail": "Unknown job."}, status=status.HTTP_404_NOT_FOUND)
        services.mark_done(job)
        return Response({"status": job.status})


@extend_schema(tags=["Print bridge"], request=BridgeFailSerializer, responses={200: dict})
class BridgeJobFailedView(_JobAck):
    def post(self, request, id):
        if request.printer is None:
            return self._unauthorised()
        job = self._job(request, id)
        if job is None:
            return Response({"detail": "Unknown job."}, status=status.HTTP_404_NOT_FOUND)
        serializer = BridgeFailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.mark_failed(job, serializer.validated_data.get("error", ""))
        return Response({"status": job.status, "attempts": job.attempts})
