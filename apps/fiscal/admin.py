from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import FiscalDocument, FiscalProfile


@admin.register(FiscalProfile)
class FiscalProfileAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["restaurant", "vat_payer", "vat_rate", "prices_include_vat", "provider", "tax_id"]
    list_filter = ["vat_payer", "provider"]
    search_fields = ["restaurant__name", "legal_name", "tax_id"]
    raw_id_fields = ["restaurant"]
    exclude = ["credentials_encrypted"]


@admin.register(FiscalDocument)
class FiscalDocumentAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = [
        "fiscal_number",
        "restaurant",
        "kind",
        "status",
        "gross_total",
        "vat_total",
        "attempts",
        "created_at",
    ]
    list_filter = ["kind", "status", "provider"]
    search_fields = ["fiscal_number", "external_id", "order__order_number"]
    readonly_fields = [f.name for f in FiscalDocument._meta.fields if f.name != "id"]
    raw_id_fields = ["restaurant", "payment", "refund", "order", "stock_lot", "reverses"]
