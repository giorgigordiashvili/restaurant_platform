from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import PurchaseOrder, Supplier, SupplierItem


@admin.register(Supplier)
class SupplierAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["name", "restaurant", "phone", "email", "is_active"]
    search_fields = ["name"]


@admin.register(SupplierItem)
class SupplierItemAdmin(TenantAwareModelAdmin):
    tenant_field = "supplier__restaurant"
    list_display = ["stock_item", "supplier", "unit", "price", "is_preferred", "last_price_at"]


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["number", "restaurant", "supplier", "status", "expected_on", "subtotal"]
    list_filter = ["status"]
