"""
Glovo menu JSON (fetched by Glovo from our public menu feed after
``upload_menu``). Ids are prefixed (``p<pk>``, ``m<pk>``, ``g<pk>``) so
inbound order payloads map back unambiguously. Field names follow the
Partners docs and are table-driven here -- a rename is a one-line change.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings

from apps.delivery.ids import attribute_id, group_id, parse_attribute_id, parse_product_id, product_id
from apps.menu.models import SELLABLE, MenuCategory, MenuItem, Modifier, ModifierGroup

__all__ = [
    "build_menu",
    "build_availability_update",
    "build_bulk_refresh",
    "product_id",
    "attribute_id",
    "group_id",
    "parse_product_id",
    "parse_attribute_id",
]


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
    if isinstance(target, Modifier):
        return {"products": [], "attributes": [{"id": attribute_id(target), "available": bool(available)}]}
    return {"products": [{"id": product_id(target), "available": bool(available)}], "attributes": []}


def build_bulk_refresh(restaurant) -> dict:
    """Prices + availability of every dish / modifier in one bulk-update body (after a price change in admin)."""
    products = [
        {
            "id": product_id(i),
            "price": _price(i.price),
            "available": bool(i.is_available and not i.auto_disabled_by_stock),
        }
        for i in MenuItem.objects.filter(restaurant=restaurant, category__is_active=True).order_by("display_order")
    ]
    attributes = [
        {
            "id": attribute_id(m),
            "price_impact": _price(m.price_adjustment),
            "available": bool(m.is_available and not m.auto_disabled_by_stock),
        }
        for m in Modifier.objects.filter(group__restaurant=restaurant, group__is_active=True).order_by("display_order")
    ]
    return {"products": products, "attributes": attributes}


def sellable_products(restaurant):  # pragma: no cover - convenience
    return MenuItem.objects.filter(SELLABLE, restaurant=restaurant)
