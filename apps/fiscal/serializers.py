from rest_framework import serializers

from .models import FiscalDocument


class FiscalDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = FiscalDocument
        fields = [
            "id",
            "kind",
            "status",
            "provider",
            "fiscal_number",
            "external_id",
            "is_fiscal",
            "payment",
            "refund",
            "order",
            "vat_rate",
            "prices_include_vat",
            "net_total",
            "vat_total",
            "gross_total",
            "vat_breakdown",
            "error",
            "attempts",
            "issued_at",
            "created_at",
        ]
        read_only_fields = fields
