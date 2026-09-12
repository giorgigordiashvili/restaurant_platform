from django.contrib import admin

from apps.core.admin import TenantAwareModelAdmin

from .models import ComboComponent, MenuSchedule, Promotion, PromotionUse


@admin.register(MenuSchedule)
class MenuScheduleAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["name", "restaurant", "start_time", "end_time", "is_active"]


@admin.register(Promotion)
class PromotionAdmin(TenantAwareModelAdmin):
    tenant_field = "restaurant"
    list_display = ["name", "restaurant", "kind", "is_active", "code", "uses_count"]
    list_filter = ["kind", "is_active"]
    search_fields = ["name", "code"]


@admin.register(PromotionUse)
class PromotionUseAdmin(TenantAwareModelAdmin):
    tenant_field = "promotion__restaurant"
    list_display = ["promotion", "order", "customer", "phone", "amount", "created_at"]


@admin.register(ComboComponent)
class ComboComponentAdmin(TenantAwareModelAdmin):
    tenant_field = "combo__restaurant"
    list_display = ["combo", "item", "quantity"]
