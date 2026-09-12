"""
Richer autocomplete results for modifier groups.

Django renders each autocomplete hit as ``str(obj)``, which for a modifier
group is its customer-facing name -- so five groups called "ექსტრა" are
indistinguishable in the menu item's picker. This view shows the staff-only
internal name, what customers see, and the first few options instead.
Installed on both the tenant admin site and the platform admin site.
"""

from django.contrib.admin.views.autocomplete import AutocompleteJsonView

from .models import ModifierGroup

OPTIONS_PREVIEW = 4


def modifier_group_picker_label(group: ModifierGroup) -> str:
    customer_name = group.safe_translation_getter("name", any_language=True) or ""
    options = [m.safe_translation_getter("name", any_language=True) or "" for m in group.modifiers.all()]
    parts = [group.internal_name or customer_name]
    if group.internal_name and customer_name:
        parts.append(f"shows as: {customer_name}")
    if options:
        preview = ", ".join(options[:OPTIONS_PREVIEW])
        if len(options) > OPTIONS_PREVIEW:
            preview += f" +{len(options) - OPTIONS_PREVIEW}"
        parts.append(f"options: {preview}")
    return " · ".join(p for p in parts if p)


class PickerAutocompleteJsonView(AutocompleteJsonView):
    """
    Site-level replacement for Django's autocomplete endpoint.

    ``/admin/autocomplete/`` is served by the AdminSite, not by a ModelAdmin,
    so this is installed on both admin sites (see ``TenantAdminSite`` and the
    patch in ``apps/core/admin.py``). Models without special needs get the
    default ``str(obj)`` text.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        if self.model_admin.model is ModifierGroup:
            qs = qs.prefetch_related("modifiers__translations", "translations")
        return qs

    def serialize_result(self, obj, to_field_name):
        result = super().serialize_result(obj, to_field_name)
        if isinstance(obj, ModifierGroup):
            result["text"] = modifier_group_picker_label(obj)
        return result
