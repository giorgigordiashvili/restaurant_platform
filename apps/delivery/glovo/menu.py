"""
Glovo menu JSON (fetched by Glovo from our public menu feed after
``upload_menu``). Ids are prefixed (``p<pk>``, ``m<pk>``, ``g<pk>``) so
inbound order payloads map back unambiguously. Field names follow the
Partners docs and are table-driven here -- a rename is a one-line change.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings

from apps.menu.models import SELLABLE, MenuCategory, MenuItem, ModifierGroup


def product_id(menu_item) -> str:
    return f"p{menu_item.pk}"


def attribute_id(modifier) -> str:
    return f"m{modifier.pk}"


def group_id(group) -> str:
    return f"g{group.pk}"


def _uuid_after(prefix: str, value) -> str | None:
    import uuid

    s = str(value or "")
    if not s.startswith(prefix) or len(s) <= len(prefix):
        return None
    try:
        return str(uuid.UUID(s[len(prefix) :]))
    except ValueError:
        return None


def parse_product_id(value) -> str | None:
    """'p<uuid>' -> uuid string (None for anything else, incl. ids we never issued)."""
    return _uuid_after("p", value)


def parse_attribute_id(value) -> str | None:
    return _uuid_after("m", value)


def _name(obj, language: str) -> str:
    return obj.safe_translation_getter("name", language_code=language, any_language=True) or str(obj.pk)


def _desc(obj, language: str) -> str:
    return obj.safe_translation_getter("description", language_code=language, any_language=True) or ""


def _image(obj) -> str | None:
    image = getattr(obj, "image", None)
    if not image:
        return None
    url = image.url
    if url.startswith("http"):
        return url
    return f"{getattr(settings, 'PUBLIC_API_BASE_URL', '').rstrip('/')}{url}"


def _price(value) -> float:
    return float(Decimal(value or 0))


def build_menu(link) -> dict:
    restaurant = link.restaurant
    language = getattr(restaurant, "default_language", None) or "ka"
    groups = list(
        ModifierGroup.objects.filter(restaurant=restaurant, is_active=True)
        .prefetch_related("modifiers", "translations")
        .order_by("display_order")
    )
    items = list(
        MenuItem.objects.filter(restaurant=restaurant, category__is_active=True)
        .select_related("category")
        .prefetch_related("translations", "modifier_groups_link__modifier_group")
        .order_by("category__display_order", "display_order")
    )
    categories = list(
        MenuCategory.objects.filter(restaurant=restaurant, is_active=True)
        .prefetch_related("translations")
        .order_by("display_order")
    )

    attributes = []
    attribute_groups = []
    for g in groups:
        mods = list(g.modifiers.all())
        for m in mods:
            attributes.append(
                {
                    "id": attribute_id(m),
                    "name": _name(m, language),
                    "price_impact": _price(m.price_adjustment),
                    "available": bool(m.is_available and not m.auto_disabled_by_stock),
                    "selected_by_default": bool(getattr(m, "is_default", False)),
                }
            )
        attribute_groups.append(
            {
                "id": group_id(g),
                "name": _name(g, language),
                "min": int(g.min_selections or 0),
                "max": int(g.max_selections or (len(mods) if g.selection_type != "single" else 1)),
                "multiple_selection": g.selection_type != "single",
                "attributes": [attribute_id(m) for m in mods],
            }
        )

    products = []
    by_category: dict = {}
    for item in items:
        links = getattr(item, "modifier_groups_link", None)
        group_ids = [group_id(x.modifier_group) for x in (links.all() if links is not None else [])]
        products.append(
            {
                "id": product_id(item),
                "name": _name(item, language),
                "description": _desc(item, language),
                "price": _price(item.price),
                "image_url": _image(item),
                "available": bool(item.is_available and not item.auto_disabled_by_stock),
                "attributes_groups": group_ids,
            }
        )
        by_category.setdefault(item.category_id, []).append(product_id(item))

    collections = [
        {
            "name": _name(c, language),
            "position": c.display_order,
            "image_url": _image(c),
            "sections": [{"name": _name(c, language), "position": 0, "products": by_category.get(c.pk, [])}],
        }
        for c in categories
        if by_category.get(c.pk)
    ]
    return {
        "attributes": attributes,
        "attribute_groups": attribute_groups,
        "products": products,
        "collections": collections,
        "supercollections": [],
    }


def build_availability_update(target, available: bool) -> dict:
    """The bulk-update body that flips one dish or one modifier."""
    from apps.menu.models import Modifier

    if isinstance(target, Modifier):
        return {"products": [], "attributes": [{"id": attribute_id(target), "available": bool(available)}]}
    return {"products": [{"id": product_id(target), "available": bool(available)}], "attributes": []}


def sellable_products(restaurant):  # pragma: no cover - convenience
    return MenuItem.objects.filter(SELLABLE, restaurant=restaurant)
