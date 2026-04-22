"""Customer-facing referral / wallet endpoints."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db.models import Sum

from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from .models import WalletTransaction
from .serializers import (
    ReferralSummarySerializer,
    ReferredUserSerializer,
    WalletTransactionSerializer,
    build_referred_user_payload,
)
from .services import effective_percent


def _frontend_base_url() -> str:
    """Best-effort frontend origin for shareable referral URLs."""
    return getattr(settings, "FRONTEND_BASE_URL", "https://aimenu.ge").rstrip("/")


@extend_schema(tags=["Referrals"])
class ReferralSummaryView(APIView):
    """Headline numbers for the current user."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        # `getattr` so accounts that haven't been migrated to a profile yet
        # don't 500 — they just see zeros and an empty referral_code.
        profile = getattr(user, "profile", None)
        code = profile.referral_code if profile else ""
        balance = Decimal(profile.wallet_balance) if profile else Decimal("0")

        ledger = WalletTransaction.objects.filter(user=user)
        total_earned = ledger.filter(
            kind__in=[
                WalletTransaction.KIND_REFERRAL_CREDIT,
                WalletTransaction.KIND_REFERRAL_CLAWBACK,
            ]
        ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
        total_spent = ledger.filter(kind=WalletTransaction.KIND_ORDER_SPEND).aggregate(s=Sum("amount"))["s"] or Decimal(
            "0"
        )
        # spent rows are negative; report as positive value.
        total_spent = -total_spent

        referred_users_count = user.referrals_made.count() if hasattr(user, "referrals_made") else 0
        percent = effective_percent(user)

        referral_url = f"{_frontend_base_url()}/register?ref={code}" if code else ""

        payload = {
            "referral_code": code,
            "referral_url": referral_url,
            "wallet_balance": balance,
            "total_earned": total_earned,
            "total_spent": total_spent,
            "referred_users_count": referred_users_count,
            "effective_percent": percent,
        }
        return Response(ReferralSummarySerializer(payload).data)


@extend_schema(tags=["Referrals"])
class WalletHistoryView(generics.ListAPIView):
    """Paginated ledger for the current user."""

    serializer_class = WalletTransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            WalletTransaction.objects.filter(user=self.request.user)
            .select_related("source_order", "referred_user")
            .order_by("-created_at")
        )


@extend_schema(tags=["Referrals"], responses=ReferredUserSerializer(many=True))
class ReferredUsersView(APIView):
    """List of users this user has referred + total earned from each."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = build_referred_user_payload(request.user)
        return Response(ReferredUserSerializer(rows, many=True).data)
