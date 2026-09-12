"""
Building blocks shared by every tenant-admin page (apps.core.tenant_admin,
apps.inventory.tenant_admin): Unfold form styling, role-based permission
helpers and the tenant-scoped ModelAdmin / inline mixins.
"""

from django import forms

from parler.admin import TranslatableAdmin
from parler.forms import TranslatableModelForm
from parler.utils.views import get_language_parameter
from unfold.admin import ModelAdmin as UnfoldModelAdmin

# Unfold input styling classes
UNFOLD_INPUT_CLASSES = (
    "border border-base-200 bg-white font-medium min-w-20 placeholder-base-400 "
    "rounded-default shadow-xs text-font-default-light text-sm focus:outline-2 "
    "focus:-outline-offset-2 focus:outline-primary-600 group-[.errors]:border-red-600 "
    "focus:group-[.errors]:outline-red-600 dark:bg-base-900 dark:border-base-700 "
    "dark:text-font-default-dark dark:group-[.errors]:border-red-500 "
    "dark:focus:group-[.errors]:outline-red-500 dark:scheme-dark "
    "group-[.primary]:border-transparent disabled:!bg-base-50 "
    "dark:disabled:!bg-base-800 px-3 py-2 w-full max-w-2xl"
)

UNFOLD_TEXTAREA_CLASSES = UNFOLD_INPUT_CLASSES + " min-h-[120px]"


class UnfoldTranslatableModelForm(TranslatableModelForm):
    """Translatable form with Unfold styling applied to all fields."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "")
                field.widget.attrs["class"] += " " + UNFOLD_TEXTAREA_CLASSES
            elif isinstance(field.widget, (forms.TextInput, forms.NumberInput, forms.EmailInput)):
                field.widget.attrs.setdefault("class", "")
                field.widget.attrs["class"] += " " + UNFOLD_INPUT_CLASSES


def staff_permissions(request):
    """Effective role permissions of request.user at request.restaurant ({} if none)."""
    restaurant = getattr(request, "restaurant", None)
    if not restaurant or not request.user.is_authenticated:
        return {}
    if request.user.is_superuser:
        return {"*": ["create", "read", "update", "delete"]}
    try:
        staff = request.user.staff_memberships.get(restaurant=restaurant, is_active=True)
        return staff.get_effective_permissions()
    except Exception:
        return {}


def has_resource_permission(request, resource, action):
    """Role-based check used by every tenant admin and inline."""
    if not resource:
        return request.user.is_superuser
    permissions = staff_permissions(request)
    if "*" in permissions:
        return True
    resource_perms = permissions.get(resource, [])
    return action in resource_perms or "*" in resource_perms


class TenantInlineMixin:
    """
    Role-based permissions for inlines on the tenant admin.

    Django gates every inline on model-level auth permissions
    (``menu.change_modifier`` ...), which restaurant staff never hold -- they
    are authorised through StaffMember roles. Without this the whole inline is
    silently dropped for them: no option rows under a modifier group, no
    modifier groups on a menu item, and rows they do post are discarded with
    no error. Put this mixin *first* so it wins over the Django defaults.
    """

    permission_resource = None
    # Which role action each admin operation needs. Override where adding or
    # removing rows is really just editing the parent (e.g. opening hours are
    # part of "settings", which only grants read/update).
    permission_actions = {"view": "read", "add": "create", "change": "update", "delete": "delete"}

    def _allowed(self, request, operation):
        return has_resource_permission(request, self.permission_resource, self.permission_actions[operation])

    def has_view_permission(self, request, obj=None):
        return self._allowed(request, "view")

    def has_add_permission(self, request, obj=None):
        return self._allowed(request, "add")

    def has_change_permission(self, request, obj=None):
        return self._allowed(request, "change")

    def has_delete_permission(self, request, obj=None):
        return self._allowed(request, "delete")


class OptionalTranslationInlineForm(TranslatableModelForm):
    """
    Inline row form for translatable children (modifier options).

    The language tabs reload the whole page in one language, so on the English
    tab every existing option shows its (still empty) English name. Parler
    would then reject the save until *all* of them are filled in. Here an
    existing row left blank simply gets no translation in that language --
    customers see the fallback -- while a brand-new row still needs a name.
    """

    @property
    def _is_existing_row(self):
        # Not ``instance.pk``: these models have UUID keys with a default, so an
        # unsaved row already carries one. ``_state.adding`` is the truth.
        return not self.instance._state.adding

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self._is_existing_row:
            for name in self._translated_fields:
                self.fields[name].required = False

    def save_translated_fields(self):
        if self._is_existing_row and all(self.cleaned_data.get(name) in (None, "") for name in self._translated_fields):
            return
        super().save_translated_fields()


class TenantLanguageDefaultMixin:
    """Open forms on the restaurant's own default language instead of always Georgian."""

    def _language(self, request, obj=None):
        restaurant = getattr(request, "restaurant", None)
        return get_language_parameter(
            request, self.query_language_key, default=getattr(restaurant, "default_language", None)
        )


class TenantForeignKeyScopingMixin:
    """
    Restrict foreign-key choices to the current restaurant.

    Applies to any related model that carries a ``restaurant`` FK (categories,
    modifier groups, sections, roles...). Without it a select lists rows from
    every tenant, and a hand-crafted POST could attach another restaurant's
    category or modifier group.
    """

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        restaurant = getattr(request, "restaurant", None)
        related = db_field.related_model
        if restaurant and related is not None and "queryset" not in kwargs:
            if any(f.name == "restaurant" for f in related._meta.get_fields()):
                kwargs["queryset"] = related._default_manager.filter(restaurant=restaurant)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class TenantModelAdmin(TenantForeignKeyScopingMixin, UnfoldModelAdmin):
    """
    Base admin class for tenant-scoped models.

    Provides:
    - Automatic filtering to current restaurant
    - Role-based permission checking
    - Restaurant auto-assignment on save
    """

    # Override in subclass: "menu", "orders", "tables", "staff", "reservations"
    permission_resource = None

    # Field name for restaurant FK (override if different)
    restaurant_field = "restaurant"

    def get_queryset(self, request):
        """Filter queryset to current restaurant only."""
        qs = super().get_queryset(request)
        restaurant = getattr(request, "restaurant", None)

        if restaurant and self.restaurant_field:
            filter_kwargs = {self.restaurant_field: restaurant}
            qs = qs.filter(**filter_kwargs)

        return qs

    def _direct_restaurant_field(self):
        """The restaurant FK on this model itself, or None when reached via a relation."""
        if self.restaurant_field and "__" not in self.restaurant_field:
            return self.restaurant_field
        return None

    def get_exclude(self, request, obj=None):
        """
        Never put the restaurant FK on the form. The tenant is fixed by the
        subdomain, and the default select would list every restaurant on the
        platform to this restaurant's staff.
        """
        exclude = list(super().get_exclude(request, obj) or [])
        field = self._direct_restaurant_field()
        if field and field not in exclude:
            exclude.append(field)
        return exclude

    def save_model(self, request, obj, form, change):
        """Pin the object to the current restaurant, since the FK is never on the form."""
        field = self._direct_restaurant_field()
        restaurant = getattr(request, "restaurant", None)
        # Check the raw *_id: hasattr() on an unset FK raises and reads as False,
        # which used to leave restaurant NULL and 500 on insert.
        if field and restaurant and getattr(obj, f"{field}_id", None) is None:
            setattr(obj, field, restaurant)
        super().save_model(request, obj, form, change)

    def _get_staff_permissions(self, request):
        """Get the current user's staff permissions for this restaurant."""
        return staff_permissions(request)

    def _has_resource_permission(self, request, action):
        """Check if user has permission for the given action on this resource."""
        return has_resource_permission(request, self.permission_resource, action)

    def has_view_permission(self, request, obj=None):
        """Check read permission."""
        return self._has_resource_permission(request, "read")

    def has_add_permission(self, request):
        """Check create permission."""
        return self._has_resource_permission(request, "create")

    def has_change_permission(self, request, obj=None):
        """Check update permission."""
        return self._has_resource_permission(request, "update")

    def has_delete_permission(self, request, obj=None):
        """Check delete permission."""
        return self._has_resource_permission(request, "delete")

    def has_module_permission(self, request):
        """Check if user can see this model in admin index."""
        return self._has_resource_permission(request, "read")


class TenantTranslatableAdmin(TenantLanguageDefaultMixin, TranslatableAdmin, TenantModelAdmin):
    """
    Combined admin for translatable models with tenant scoping.

    Use this for models that use django-parler for translations.
    """

    def get_form(self, request, obj=None, **kwargs):
        """Apply Unfold styling to translatable form fields."""
        form = super().get_form(request, obj, **kwargs)

        # Apply Unfold classes to all form fields
        for field_name, field in form.base_fields.items():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "")
                field.widget.attrs["class"] += " " + UNFOLD_TEXTAREA_CLASSES
            elif isinstance(field.widget, (forms.TextInput, forms.NumberInput, forms.EmailInput)):
                field.widget.attrs.setdefault("class", "")
                field.widget.attrs["class"] += " " + UNFOLD_INPUT_CLASSES

        return form
