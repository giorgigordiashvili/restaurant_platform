"""DRF serializers for referral endpoints."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from rest_framework import serializers

from apps.accounts.models import User

from .models import WalletTransaction


class WalletTransactionSerializer(serializers.ModelSerializer):
    source_order_number = serializers.CharField(source="source_order.order_number", default=None, read_only=True)
    referred_user_email = serializers.CharField(source="referred_user.email", default=None, read_only=True)

    class Meta:
        model = WalletTransaction
        fields = [
            "id",
            "kind",
            "amount",
            "balance_after",
            "source_order",
            "source_order_number",
            "referred_user",
            "referred_user_email",
            "notes",
            "created_at",
        ]
        read_only_fields = fields


class ReferralSummarySerializer(serializers.Serializer):
    """Read-only headline numbers for /api/v1/referrals/me/."""

    referral_code = serializers.CharField()
    referral_url = serializers.CharField()
    wallet_balance = serializers.DecimalField(max_digits=10, decimal_places=2)
    total_earned = serializers.DecimalField(max_digits=10, decimal_places=2)
    total_spent = serializers.DecimalField(max_digits=10, decimal_places=2)
    referred_users_count = serializers.IntegerField()
    effective_percent = serializers.DecimalField(max_digits=5, decimal_places=2)


class ReferredUserSerializer(serializers.Serializer):
    """One row per user this user has referred, including aggregate earned-from-them."""

    id = serializers.UUIDField()
    email = serializers.EmailField()
    full_name = serializers.CharField()
    joined_at = serializers.DateTimeField()
    total_earned = serializers.DecimalField(max_digits=10, decimal_places=2)


def build_referred_user_payload(referrer: User) -> list[dict]:
    """Lookup helper used by both the list view and the summary view."""
    referred_qs = User.objects.filter(profile__referred_by=referrer)
    rows = []
    for user in referred_qs.select_related("profile").order_by("-created_at"):
        earned = WalletTransaction.objects.filter(
            user=referrer,
            referred_user=user,
            kind__in=[
                WalletTransaction.KIND_REFERRAL_CREDIT,
                WalletTransaction.KIND_REFERRAL_CLAWBACK,
            ],
        ).aggregate(s=Sum("amount"))["s"] or Decimal("0")
        rows.append(
            {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "joined_at": user.created_at,
                "total_earned": earned,
            }
        )
    return rows
