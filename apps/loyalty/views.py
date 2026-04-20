from __future__ import annotations

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.middleware.tenant import get_current_restaurant, require_restaurant
from apps.core.permissions import IsTenantManager

from .models import LoyaltyCounter, LoyaltyProgram, LoyaltyRedemption
from .serializers import (
    LoyaltyConfirmRequestSerializer,
    LoyaltyCounterSerializer,
    LoyaltyProgramSerializer,
    LoyaltyProgramWriteSerializer,
    LoyaltyRedeemRequestSerializer,
    LoyaltyRedemptionSerializer,
    LoyaltyValidateRequestSerializer,
)


def _err(code: str, message: str, status_code: int = status.HTTP_400_BAD_REQUEST, details=None):
    return Response(
        {"success": False, "error": {"code": code, "message": message, "details": details}},
        status=status_code,
    )


# ─── Customer-side ────────────────────────────────────────────────────────────


@extend_schema(tags=["Loyalty"])
class CustomerLoyaltyListView(generics.ListAPIView):
    """My counters across all restaurants."""

    serializer_class = LoyaltyCounterSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        # Direct user-bound counters
        qs = (
            LoyaltyCounter.objects.filter(user=user, program__is_active=True)
            .select_related("program__trigger_item", "program__reward_item", "program__restaurant")
            .order_by("-updated_at")
        )
        return qs


@extend_schema(tags=["Loyalty"])
class CustomerLoyaltyRedeemView(APIView):
    """Generate a redemption code for one of my eligible counters."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LoyaltyRedeemRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        program_id = serializer.validated_data["program_id"]

        try:
            program = LoyaltyProgram.objects.get(id=program_id, is_active=True)
        except LoyaltyProgram.DoesNotExist:
            return _err("not_found", "Program not found.", status.HTTP_404_NOT_FOUND)

        if not program.is_live():
            return _err("not_live", "This program is not currently running.")

        counter = LoyaltyCounter.objects.filter(program=program, user=request.user).first()
        if not counter:
            return _err("no_counter", "You have no punches yet for this program.")

        if counter.punches < program.threshold:
            return _err(
                "insufficient",
                f"You need {program.threshold} punches to redeem (have {counter.punches}).",
            )

        now = timezone.now()
        existing = (
            LoyaltyRedemption.objects.filter(
                counter=counter,
                user=request.user,
                status=LoyaltyRedemption.STATUS_PENDING,
                expires_at__gt=now,
            )
            .order_by("-issued_at")
            .first()
        )
        if existing:
            return Response(
                {"success": True, "data": LoyaltyRedemptionSerializer(existing).data}
            )

        redemption = LoyaltyRedemption.objects.create(
            program=program,
            counter=counter,
            user=request.user,
            issued_at=now,
        )
        return Response(
            {"success": True, "data": LoyaltyRedemptionSerializer(redemption).data},
            status=status.HTTP_201_CREATED,
        )


# ─── Dashboard-side ───────────────────────────────────────────────────────────


@extend_schema(tags=["Dashboard - Loyalty"])
class DashboardProgramListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated, IsTenantManager]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return LoyaltyProgramWriteSerializer
        return LoyaltyProgramSerializer

    @require_restaurant
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @require_restaurant
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def get_queryset(self):
        restaurant = get_current_restaurant(self.request)
        return (
            LoyaltyProgram.objects.filter(restaurant=restaurant)
            .select_related("trigger_item", "reward_item")
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["restaurant"] = get_current_restaurant(self.request)
        return ctx

    def perform_create(self, serializer):
        restaurant = get_current_restaurant(self.request)
        serializer.save(restaurant=restaurant)


@extend_schema(tags=["Dashboard - Loyalty"])
class DashboardProgramDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated, IsTenantManager]

    def get_serializer_class(self):
        if self.request.method in ("PUT", "PATCH"):
            return LoyaltyProgramWriteSerializer
        return LoyaltyProgramSerializer

    def get_queryset(self):
        restaurant = get_current_restaurant(self.request)
        return LoyaltyProgram.objects.filter(restaurant=restaurant)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["restaurant"] = get_current_restaurant(self.request)
        return ctx

    @require_restaurant
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @require_restaurant
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @require_restaurant
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)


@extend_schema(tags=["Dashboard - Loyalty"])
class DashboardValidateView(APIView):
    """Staff types/scans a code; returns the redemption context (no state
    change). Used to pre-fill the confirm screen on the POS."""

    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def post(self, request):
        serializer = LoyaltyValidateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        restaurant = get_current_restaurant(request)

        try:
            redemption = LoyaltyRedemption.objects.select_related(
                "program__trigger_item",
                "program__reward_item",
                "program__restaurant",
                "user",
                "counter",
            ).get(code=serializer.validated_data["code"], program__restaurant=restaurant)
        except LoyaltyRedemption.DoesNotExist:
            return _err("not_found", "Code not found.", status.HTTP_404_NOT_FOUND)

        if not redemption.is_usable:
            reason = "expired" if redemption.status == LoyaltyRedemption.STATUS_PENDING else redemption.status
            return _err(reason, f"Code not usable ({reason}).")

        return Response({"success": True, "data": LoyaltyRedemptionSerializer(redemption).data})


@extend_schema(tags=["Dashboard - Loyalty"])
class DashboardConfirmView(APIView):
    """Commit the redemption: counter -= threshold, status = redeemed."""

    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def post(self, request):
        serializer = LoyaltyConfirmRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        restaurant = get_current_restaurant(request)
        code = serializer.validated_data["code"]
        order_id = serializer.validated_data.get("order_id")

        with transaction.atomic():
            try:
                redemption = (
                    LoyaltyRedemption.objects.select_for_update()
                    .select_related("program", "counter")
                    .get(code=code, program__restaurant=restaurant)
                )
            except LoyaltyRedemption.DoesNotExist:
                return _err("not_found", "Code not found.", status.HTTP_404_NOT_FOUND)

            if redemption.status != LoyaltyRedemption.STATUS_PENDING:
                return _err("already_handled", f"Already {redemption.status}.")
            now = timezone.now()
            if redemption.expires_at < now:
                redemption.status = LoyaltyRedemption.STATUS_EXPIRED
                redemption.save(update_fields=["status", "updated_at"])
                return _err("expired", "Code has expired.")

            program = redemption.program
            counter = redemption.counter

            if counter.punches < program.threshold:
                return _err(
                    "insufficient",
                    "Counter dropped below threshold (concurrent redeem?).",
                )

            LoyaltyCounter.objects.filter(pk=counter.pk).update(
                punches=F("punches") - program.threshold,
                updated_at=now,
            )

            redemption.status = LoyaltyRedemption.STATUS_REDEEMED
            redemption.redeemed_at = now
            redemption.redeemed_by = request.user
            if order_id:
                redemption.redeemed_order_id = order_id
            redemption.save(update_fields=[
                "status",
                "redeemed_at",
                "redeemed_by",
                "redeemed_order",
                "updated_at",
            ])

        return Response(
            {"success": True, "data": LoyaltyRedemptionSerializer(redemption).data}
        )
