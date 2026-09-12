"""Tenant admin: promotions and menu schedules (CRUD), combo components inline on dishes."""

from __future__ import annotations

from django import forms
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from unfold.admin import TabularInline
from unfold.decorators import display

from apps.core.tenant_admin_base import ModuleEnabledMixin, TenantInlineMixin, TenantModelAdmin
from apps.promotions.models import WEEKDAYS, ComboComponent, MenuSchedule, Promotion


class PromotionsEnabledMixin(ModuleEnabledMixin):
    module_code = "promotions"


class MenuScheduleForm(forms.ModelForm):
    weekdays = forms.TypedMultipleChoiceField(
        choices=WEEKDAYS, coerce=int, required=False, widget=forms.CheckboxSelectMultiple, label=_("Days")
    )

    class Meta:
        model = MenuSchedule
        fields = ["name", "weekdays", "start_time", "end_time", "is_active"]


class MenuScheduleTenantAdmin(PromotionsEnabledMixin, TenantModelAdmin):
    permission_resource = "menu"
    restaurant_field = "restaurant"
    form = MenuScheduleForm
    list_display = ["name", "window", "days", "is_active", "used_by"]
    list_filter = ["is_active"]
    search_fields = ["name"]

    @display(description=_("Window"))
    def window(self, obj):
        return f"{obj.start_time:%H:%M}–{obj.end_time:%H:%M}"

    @display(description=_("Days"))
    def days(self, obj):
        days = [int(d) for d in (obj.weekdays or [])]
        if not days or len(days) == 7:
            return _("Every day")
        return ", ".join(str(dict(WEEKDAYS)[d])[:3] for d in sorted(days))

    @display(description=_("Used by"))
    def used_by(self, obj):
        return f"{obj.categories.count()} {_('categories')} · {obj.items.count()} {_('dishes')} · {obj.promotions.count()} {_('promotions')}"

    def save_model(self, request, obj, form, change):
        if not obj.restaurant_id:
            obj.restaurant = request.restaurant
        super().save_model(request, obj, form, change)


class PromotionForm(forms.ModelForm):
    channels = forms.MultipleChoiceField(
        choices=[("web", _("Website")), ("qr", _("QR table")), ("pos", _("POS"))],
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label=_("Channels"),
        help_text=_("Empty = everywhere."),
    )

    class Meta:
        model = Promotion
        fields = [
            "name",
            "description",
            "kind",
            "is_active",
            "mode",
            "value",
            "applies_to",
            "categories",
            "items",
            "schedule",
            "code",
            "starts_on",
            "ends_on",
            "min_order_amount",
            "max_uses",
            "max_uses_per_customer",
            "channels",
            "stackable",
        ]

    def clean(self):
        data = super().clean()
        if data.get("kind") == "promo_code" and not (data.get("code") or "").strip():
            self.add_error("code", _("A promo code needs a code."))
        if data.get("kind") == "happy_hour" and not data.get("schedule"):
            self.add_error("schedule", _("A happy hour needs a schedule (when it is on)."))
        if data.get("mode") == "percent" and data.get("value") and data["value"] > 100:
            self.add_error("value", _("Percent cannot exceed 100."))
        return data


class PromotionTenantAdmin(PromotionsEnabledMixin, TenantModelAdmin):
    permission_resource = "menu"
    restaurant_field = "restaurant"
    form = PromotionForm
    list_display = [
        "name",
        "kind",
        "is_active",
        "live",
        "discount",
        "applies_to",
        "code",
        "uses_count",
        "discount_total",
    ]
    list_filter = ["kind", "is_active", "applies_to"]
    search_fields = ["name", "code"]
    autocomplete_fields = ["categories", "items"]
    actions = ["activate", "deactivate"]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        restaurant = getattr(request, "restaurant", None)
        if "schedule" in form.base_fields:
            form.base_fields["schedule"].queryset = MenuSchedule.objects.filter(restaurant=restaurant)
        return form

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("schedule").annotate(_discount_total=Sum("uses__amount"))

    @display(description=_("Live now"), boolean=True)
    def live(self, obj):
        from apps.promotions.availability import local_now

        return obj.is_live(local_now(obj.restaurant))

    @display(description=_("Discount"))
    def discount(self, obj):
        return f"{obj.value}%" if obj.mode == "percent" else f"-{obj.value}"

    @display(description=_("Discount total"))
    def discount_total(self, obj):
        return getattr(obj, "_discount_total", None) or 0

    def activate(self, request, queryset):
        queryset.update(is_active=True)

    activate.short_description = _("Activate")

    def deactivate(self, request, queryset):
        queryset.update(is_active=False)

    deactivate.short_description = _("Deactivate")

    def save_model(self, request, obj, form, change):
        if not obj.restaurant_id:
            obj.restaurant = request.restaurant
        super().save_model(request, obj, form, change)


class ComboComponentInline(TenantInlineMixin, TabularInline):
    """Components of a set menu; shown on every dish, only meaningful when 'is_combo' is ticked."""

    permission_resource = "menu"
    model = ComboComponent
    fk_name = "combo"
    extra = 0
    autocomplete_fields = ["item"]
    fields = ["item", "quantity", "display_order"]
    verbose_name = _("Combo component")
    verbose_name_plural = _("Combo components (tick 'Is combo' on the dish)")

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        restaurant = getattr(request, "restaurant", None)
        if "item" in formset.form.base_fields and restaurant is not None:
            from apps.menu.models import MenuItem

            formset.form.base_fields["item"].queryset = MenuItem.objects.filter(restaurant=restaurant, is_combo=False)
        return formset


def register_promotions_admin(site):
    site.register(Promotion, PromotionTenantAdmin)
    site.register(MenuSchedule, MenuScheduleTenantAdmin)
