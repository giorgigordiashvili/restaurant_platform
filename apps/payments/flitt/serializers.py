"""
Flitt-facing serializers — mirror ``apps.payments.bog.serializers`` so the
frontend can pick a provider with the same shape.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.payments.bog.serializers import InitiatePaymentSerializer as BogInitiatePaymentSerializer


class FlittInitiatePaymentSerializer(BogInitiatePaymentSerializer):
    """
    Reuse BOG's initiate shape verbatim — the two providers take the same
    target discriminator + payload, only the downstream HTTP call differs.
    """


class FlittStatusResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    settlement_status = serializers.CharField(required=False, allow_blank=True)
    order_number = serializers.CharField(allow_null=True, required=False)
    reservation_id = serializers.CharField(allow_null=True, required=False)
    session_id = serializers.CharField(allow_null=True, required=False)
    flow_type = serializers.CharField(allow_blank=True, required=False)
