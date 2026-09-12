"""
Wolt menu JSON for ``POST /v1/restaurants/{venueId}/menu``. Our ids travel
in ``external_data`` (dish ``p<uuid>``, option group ``g<uuid>``, option
value ``m<uuid>``) and come back as ``pos_id`` / ``value_pos_id`` on orders.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings

from apps.delivery.ids import attribute_id, group_id, product_id
from apps.menu.models import MenuCategory, MenuItem, Modifier, ModifierGroup

LANGS = ("ka", "en", "ru")


def _texts(obj, field: str, primary: str) -> list[dict]:
    out = []
    for lang in (primary, *[x for x in LANGS if x != primary]):
        if not obj.has_translation(lang):
            continue
        value = obj.safe_translation_getter(field, language_code=lang, any_language=False)
        if value:
            out.append({"lang": lang, "value": str(value)})
    if not out:
        value = obj.safe_translation_getter(field, any_language=True) or (str(obj.pk) if field == "name" else "")
        if value:
            out.append({"lang": primary, "value": str(value)})
    return out


def _image(obj) -> str | None:
    image = getattr(obj, "image", None)
    if not image:
        return None
    url = image.url
    if url.startswith("http"):
        return url
    return f"{getattr(settings, 'PUBLIC_API_BASE_URL', '').rstrip('/')}{url}"


def _major(value) -> float:
    return float(Decimal(value or 0).quantize(Decimal("0.01")))


def _minor(value) -> int:
    return int((Decimal(value or 0) * 100).quantize(Decimal("1")))


def _available(obj) -> bool:
    return bool(obj.is_available and not obj.auto_disabled_by_stock)


def _option(group, primary: str) -> dict:
    mods = list(group.modifiers.all())
    single = group.selection_type == "single"
    max_sel = int(group.max_selections or (1 if single else len(mods)))
    min_sel = int(group.min_selections or (1 if getattr(group, "is_required", False) else 0))
    return {
        "name": _texts(group, "name", primary),
        "type": "SingleChoice" if single else "MultiChoice",
        "selection_range": {"min": min(min_sel, max_sel), "max": max(max_sel, 1)},
        "external_data": group_id(group),
        "values": [
            {
                "name": _texts(m, "name", primary),
                "price": _major(m.price_adjustment),
                "enabled": _available(m),
                "default": bool(getattr(m, "is_default", False)),
                "external_data": attribute_id(m),
            }
            for m in mods
        ],
    }


def build_menu(link) -> dict:
    restaurant = link.restaurant
    primary = getattr(restaurant, "default_language", None) or "ka"
    currency = getattr(restaurant, "default_currency", None) or "GEL"
    groups = {
        g.pk: g
        for g in ModifierGroup.objects.filter(restaurant=restaurant, is_active=True)
        .prefetch_related("modifiers", "modifiers__translations", "translations")
        .order_by("display_order")
    }
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
    by_category: dict = {}
    for item in items:
        links = getattr(item, "modifier_groups_link", None)
        option_groups = [
            groups[x.modifier_group_id]
            for x in (links.all() if links is not None else [])
            if x.modifier_group_id in groups
        ]
        entry = {
            "name": _texts(item, "name", primary),
            "description": _texts(item, "description", primary),
            "price": _major(item.price),
            "enabled": _available(item),
            "external_data": product_id(item),
            "delivery_methods": ["homedelivery", "takeaway"],
            "options": [_option(g, primary) for g in option_groups],
        }
        image = _image(item)
        if image:
            entry["image_url"] = image
        by_category.setdefault(item.category_id, []).append(entry)
    return {
        "id": f"aimenu-{restaurant.slug}",
        "currency": currency,
        "primary_language": primary,
        "categories": [
            {
                "id": f"c{c.pk}",
                "name": _texts(c, "name", primary),
                "description": _texts(c, "description", primary),
                "items": by_category.get(c.pk, []),
            }
            for c in categories
            if by_category.get(c.pk)
        ],
    }


def build_item_update(target, available: bool) -> tuple[str, list[dict]]:
    """('items' | 'options', data) -- the PATCH body that flips one dish or one modifier."""
    if isinstance(target, Modifier):
        return "options", [{"external_id": attribute_id(target), "enabled": bool(available)}]
    return "items", [{"external_id": product_id(target), "enabled": bool(available), "in_stock": bool(available)}]


def build_bulk_refresh(restaurant) -> dict:
    """Prices (minor units) + availability of every dish / modifier for the items / options PATCH calls."""
    items = [
        {"external_id": product_id(i), "enabled": _available(i), "in_stock": _available(i), "price": _minor(i.price)}
        for i in MenuItem.objects.filter(restaurant=restaurant, category__is_active=True).order_by("display_order")
    ]
    options = [
        {"external_id": attribute_id(m), "enabled": _available(m), "price": _minor(m.price_adjustment)}
        for m in Modifier.objects.filter(group__restaurant=restaurant, group__is_active=True).order_by("display_order")
    ]
    return {"items": items, "options": options}
