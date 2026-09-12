"""
Menu import from a delivery platform into our menu (categories, dishes,
modifier groups, images), so a restaurant that already sells on Wolt does
not retype its menu. Wolt exposes the venue menu through its Menu API
(``GET /v2/venues/{venueId}/menu``); Glovo's restaurant API cannot read a
menu back, so Glovo restaurants import from Wolt (or type once) and push.

Two steps: ``fetch_preview`` (network, no writes beyond the MenuImport row)
and ``apply`` (writes, no network; images are downloaded by a task).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

from apps.core.enqueue import enqueue
from apps.delivery.errors import PlatformClientError
from apps.delivery.models import MenuImport
from apps.menu.models import MenuCategory, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup

logger = logging.getLogger(__name__)

LANGS = ("ka", "en", "ru")
CENT = Decimal("0.01")


class ImportError_(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


# ── normalised shape ──────────────────────────────────────────────────────


@dataclass
class ImportedValue:
    external_id: str
    names: dict
    price: Decimal = Decimal("0")
    enabled: bool = True
    default: bool = False


@dataclass
class ImportedOption:
    external_id: str
    names: dict
    multiple: bool
    min: int
    max: int
    values: list[ImportedValue] = field(default_factory=list)


@dataclass
class ImportedItem:
    external_id: str
    names: dict
    descriptions: dict
    price: Decimal
    image_url: str = ""
    enabled: bool = True
    options: list[ImportedOption] = field(default_factory=list)


@dataclass
class ImportedCategory:
    external_id: str
    names: dict
    descriptions: dict
    items: list[ImportedItem] = field(default_factory=list)


@dataclass
class ImportedMenu:
    source: str
    currency: str
    primary_language: str
    categories: list[ImportedCategory] = field(default_factory=list)
    price_units: str = "major"  # what we decided the payload's numbers mean

    @property
    def item_count(self) -> int:
        return sum(len(c.items) for c in self.categories)


# ── Wolt ──────────────────────────────────────────────────────────────────


def _texts(value) -> dict:
    """Wolt multilingual list [{lang, value}] (or a plain string) -> {lang: value}."""
    out = {}
    if isinstance(value, str):
        return {"": value.strip()} if value.strip() else {}
    for entry in value or []:
        if isinstance(entry, dict) and entry.get("value"):
            out[str(entry.get("lang") or "")[:2]] = str(entry["value"]).strip()
    return out


def _enabled(value) -> bool:
    if isinstance(value, dict):
        return bool(value.get("enabled", True))
    return True if value is None else bool(value)


def _num(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return Decimal("0")


def detect_price_units(numbers: list[Decimal]) -> str:
    """Wolt's menu read returns numbers; 12.5 is clearly major units, 1250 with no fractions is minor."""
    numbers = [n for n in numbers if n > 0]
    if not numbers:
        return "major"
    if any(n != n.to_integral_value() for n in numbers):
        return "major"
    numbers.sort()
    median = numbers[len(numbers) // 2]
    return "minor" if median >= 500 else "major"


def _money(value, units: str) -> Decimal:
    n = _num(value)
    if units == "minor":
        n = n / 100
    return n.quantize(CENT, rounding=ROUND_HALF_UP)


def normalize_wolt_menu(payload: dict, *, price_units: str = "auto") -> ImportedMenu:
    menu = payload.get("menu") if isinstance(payload, dict) and isinstance(payload.get("menu"), dict) else payload
    menu = menu or {}
    raw_items = {str(i.get("id")): i for i in (menu.get("items") or []) if isinstance(i, dict)}
    raw_options = {str(o.get("id")): o for o in (menu.get("options") or []) if isinstance(o, dict)}
    units = price_units
    if units == "auto":
        nums = [_num(i.get("price")) for i in raw_items.values()]
        nums += [_num(v.get("price")) for o in raw_options.values() for v in (o.get("values") or [])]
        units = detect_price_units(nums)

    def option_of(oid: str, binding: dict | None = None) -> ImportedOption | None:
        o = raw_options.get(oid)
        if o is None:
            return None
        product = o.get("product") if isinstance(o.get("product"), dict) else {}
        rng = (binding or {}).get("selection_range") or o.get("selection_range") or {}
        values = []
        for v in o.get("values") or []:
            vp = v.get("product") if isinstance(v.get("product"), dict) else {}
            values.append(
                ImportedValue(
                    external_id=str(v.get("id") or ""),
                    names=_texts(v.get("name") or vp.get("name")),
                    price=_money(v.get("price"), units),
                    enabled=_enabled(v.get("enabled")),
                    default=bool(v.get("default")),
                )
            )
        max_sel = int(rng.get("max") or 0) or (
            1 if str(o.get("type", "")).lower().startswith("single") else len(values)
        )
        return ImportedOption(
            external_id=oid,
            names=_texts(o.get("name") or product.get("name")),
            multiple=not str(o.get("type", "")).lower().startswith("single") and max_sel != 1,
            min=int(rng.get("min") or 0),
            max=max(max_sel, 1),
            values=values,
        )

    def item_of(iid: str) -> ImportedItem | None:
        i = raw_items.get(iid)
        if i is None:
            return None
        product = i.get("product") if isinstance(i.get("product"), dict) else {}
        options = []
        for b in i.get("option_bindings") or []:
            opt = option_of(str(b.get("option_id") or b.get("id") or ""), b) if isinstance(b, dict) else None
            if opt is not None:
                options.append(opt)
        # Inline options (upload shape) -- tolerate both
        for o in i.get("options") or []:
            if isinstance(o, dict) and o.get("values"):
                raw_options.setdefault(str(o.get("id") or o.get("external_data") or id(o)), o)
                opt = option_of(str(o.get("id") or o.get("external_data") or id(o)))
                if opt is not None:
                    options.append(opt)
        return ImportedItem(
            external_id=iid,
            names=_texts(i.get("name") or product.get("name")),
            descriptions=_texts(i.get("description") or product.get("description")),
            price=_money(i.get("price"), units),
            image_url=str(i.get("image_url") or product.get("image_url") or ""),
            enabled=_enabled(i.get("enabled")),
            options=options,
        )

    categories = []
    seen = set()
    for c in menu.get("categories") or []:
        if not isinstance(c, dict):
            continue
        ids = [
            str(b.get("item_id")) for b in (c.get("item_bindings") or []) if isinstance(b, dict) and b.get("item_id")
        ]
        items = [item_of(i) for i in ids]
        for inline in c.get("items") or []:  # upload shape: items inline
            if isinstance(inline, dict):
                key = str(inline.get("id") or inline.get("external_data") or id(inline))
                raw_items.setdefault(key, inline)
                items.append(item_of(key))
        items = [i for i in items if i is not None]
        seen.update(i.external_id for i in items)
        if items:
            categories.append(
                ImportedCategory(
                    external_id=str(c.get("id") or ""),
                    names=_texts(c.get("name")),
                    descriptions=_texts(c.get("description")),
                    items=items,
                )
            )
    orphans = [item_of(i) for i in raw_items if i not in seen]
    orphans = [i for i in orphans if i is not None]
    if orphans:
        categories.append(
            ImportedCategory(external_id="", names={"en": "Other", "ka": "სხვა"}, descriptions={}, items=orphans)
        )
    return ImportedMenu(
        source="wolt",
        currency=str(menu.get("currency") or ""),
        primary_language=str(menu.get("primary_language") or "")[:2],
        categories=categories,
        price_units=units,
    )


# ── matching against what the restaurant already has ──────────────────────


def _index(objects) -> dict:
    """{lowercased name in any language: object}."""
    out = {}
    for obj in objects:
        for tr in obj.translations.all():
            name = (getattr(tr, "name", "") or "").strip().lower()
            if name:
                out.setdefault(name, obj)
    return out


def _lookup(index: dict, names: dict):
    for value in names.values():
        hit = index.get((value or "").strip().lower())
        if hit is not None:
            return hit
    return None


def _label(names: dict, primary: str) -> str:
    return names.get(primary) or names.get("ka") or names.get("en") or next(iter(names.values()), "") or ""


def build_preview(restaurant, imported: ImportedMenu) -> dict:
    primary = getattr(restaurant, "default_language", None) or "ka"
    items_idx = _index(MenuItem.objects.filter(restaurant=restaurant).prefetch_related("translations"))
    cats_idx = _index(MenuCategory.objects.filter(restaurant=restaurant).prefetch_related("translations"))
    cats = []
    new = existing = 0
    for c in imported.categories:
        rows = []
        for i in c.items:
            hit = _lookup(items_idx, i.names)
            rows.append(
                {
                    "name": _label(i.names, primary),
                    "price": str(i.price),
                    "existing": hit is not None,
                    "existing_price": str(hit.price) if hit is not None else None,
                    "options": len(i.options),
                    "image": bool(i.image_url),
                    "languages": sorted(k for k in i.names if k),
                }
            )
            if hit is not None:
                existing += 1
            else:
                new += 1
        cats.append(
            {
                "name": _label(c.names, primary),
                "existing": _lookup(cats_idx, c.names) is not None,
                "items": rows,
            }
        )
    return {
        "source": imported.source,
        "currency": imported.currency,
        "price_units": imported.price_units,
        "primary_language": imported.primary_language,
        "categories": cats,
        "counts": {"categories": len(cats), "items": new + existing, "new": new, "existing": existing},
    }


# ── the two steps ─────────────────────────────────────────────────────────


def fetch_preview(link, *, by=None, price_units: str = "auto", session=None) -> MenuImport:
    """Pull the platform menu and store it (raw + preview) on a MenuImport row. Network, no menu writes."""
    from apps.delivery import services

    if link.platform != "wolt":
        raise ImportError_("not_supported", "Only Wolt can send its menu back through the API.")
    row = MenuImport.objects.create(
        link=link,
        restaurant=link.restaurant,
        source=link.platform,
        status="fetching",
        options={"price_units": price_units},
        triggered_by=by if getattr(by, "is_authenticated", False) else None,
    )
    try:
        payload = services.client_for(link, session=session).get_menu()
        imported = normalize_wolt_menu(payload, price_units=price_units)
        preview = build_preview(link.restaurant, imported)
    except PlatformClientError as exc:
        row.status = "failed"
        row.error = str(exc)[:1000]
        row.save(update_fields=["status", "error", "updated_at"])
        raise ImportError_("platform_error", str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        row.status = "failed"
        row.error = str(exc)[:1000]
        row.save(update_fields=["status", "error", "updated_at"])
        raise ImportError_("bad_menu", f"Could not read the menu: {exc}") from exc
    if not imported.categories:
        row.status = "failed"
        row.error = "The platform returned an empty menu."
        row.save(update_fields=["status", "error", "updated_at"])
        raise ImportError_("empty_menu", row.error)
    row.raw = payload
    row.preview = preview
    row.status = "previewed"
    row.save(update_fields=["raw", "preview", "status", "updated_at"])
    return row


def _set_translations(obj, names: dict, descriptions: dict | None, primary: str) -> None:
    langs = [lang for lang in LANGS if names.get(lang)]
    if not langs:
        # a language-less name (plain string) or an unsupported code -> the restaurant's primary language
        fallback = _label(names, primary)
        if not fallback:
            return
        names = {primary: fallback}
        descriptions = {primary: _label(descriptions or {}, primary)} if descriptions else {}
        langs = [primary]
    for lang in langs:
        obj.set_current_language(lang)
        obj.name = names[lang][:200]
        if descriptions is not None and hasattr(obj, "description"):
            obj.description = (descriptions.get(lang) or "")[:2000]
        obj.save()


def _group_signature(names: dict, values: list) -> str:
    return "|".join([_label(names, "ka").lower(), *sorted(_label(v.names, "ka").lower() for v in values)])


def apply(menu_import: MenuImport, *, by=None, update_prices: bool = False, download_images: bool = True) -> dict:
    """Create what is missing (categories, dishes, modifier groups); never delete; images fetched by a task."""
    if menu_import.status not in ("previewed", "failed_apply"):
        raise ImportError_("bad_state", f"Import is {menu_import.status}, expected previewed.")
    restaurant = menu_import.restaurant
    primary = getattr(restaurant, "default_language", None) or "ka"
    price_units = (menu_import.options or {}).get("price_units", "auto")
    imported = normalize_wolt_menu(menu_import.raw, price_units=price_units)
    stats = {
        "categories_created": 0,
        "items_created": 0,
        "items_updated": 0,
        "items_skipped": 0,
        "groups_created": 0,
        "images_queued": 0,
    }
    images: list[tuple[str, str]] = []
    with transaction.atomic():
        MenuImport.objects.select_for_update().filter(pk=menu_import.pk).exists()
        items_idx = _index(MenuItem.objects.filter(restaurant=restaurant).prefetch_related("translations"))
        cats_idx = _index(MenuCategory.objects.filter(restaurant=restaurant).prefetch_related("translations"))
        groups = list(
            ModifierGroup.objects.filter(restaurant=restaurant).prefetch_related(
                "translations", "modifiers__translations"
            )
        )
        groups_idx = {}
        for g in groups:
            gnames = {tr.language_code: tr.name for tr in g.translations.all()}
            values = [
                ImportedValue(external_id="", names={tr.language_code: tr.name for tr in m.translations.all()})
                for m in g.modifiers.all()
            ]
            groups_idx.setdefault(_group_signature(gnames, values), g)
        cat_order = MenuCategory.objects.filter(restaurant=restaurant).count()
        for c in imported.categories:
            category = _lookup(cats_idx, c.names)
            if category is None:
                category = MenuCategory.objects.create(restaurant=restaurant, display_order=cat_order, is_active=True)
                cat_order += 1
                _set_translations(category, c.names, c.descriptions, primary)
                for value in c.names.values():
                    cats_idx.setdefault(value.strip().lower(), category)
                stats["categories_created"] += 1
            item_order = MenuItem.objects.filter(category=category).count()
            for i in c.items:
                existing = _lookup(items_idx, i.names)
                if existing is not None:
                    if update_prices and existing.price != i.price:
                        existing.price = i.price
                        existing.save(update_fields=["price", "updated_at"])
                        stats["items_updated"] += 1
                    else:
                        stats["items_skipped"] += 1
                    if download_images and i.image_url and not existing.image:
                        images.append((str(existing.pk), i.image_url))
                    continue
                item = MenuItem.objects.create(
                    restaurant=restaurant,
                    category=category,
                    price=i.price,
                    is_available=i.enabled,
                    display_order=item_order,
                )
                item_order += 1
                _set_translations(item, i.names, i.descriptions, primary)
                for value in i.names.values():
                    items_idx.setdefault(value.strip().lower(), item)
                stats["items_created"] += 1
                for pos, opt in enumerate(i.options):
                    sig = _group_signature(opt.names, opt.values)
                    group = groups_idx.get(sig)
                    if group is None:
                        group = ModifierGroup.objects.create(
                            restaurant=restaurant,
                            selection_type="multiple" if opt.multiple else "single",
                            min_selections=opt.min,
                            max_selections=max(opt.max, 1),
                            is_required=opt.min > 0,
                            display_order=len(groups_idx),
                            internal_name=f"{menu_import.source}:{opt.external_id}"[:150] if opt.external_id else "",
                        )
                        _set_translations(group, opt.names, None, primary)
                        for vpos, v in enumerate(opt.values):
                            modifier = Modifier.objects.create(
                                group=group,
                                price_adjustment=v.price,
                                is_available=v.enabled,
                                is_default=v.default,
                                display_order=vpos,
                            )
                            _set_translations(modifier, v.names, None, primary)
                        groups_idx[sig] = group
                        stats["groups_created"] += 1
                    MenuItemModifierGroup.objects.get_or_create(
                        menu_item=item, modifier_group=group, defaults={"display_order": pos}
                    )
                if download_images and i.image_url:
                    images.append((str(item.pk), i.image_url))
        stats["images_queued"] = len(images)
        menu_import.status = "imported" if not images else "images"
        menu_import.stats = stats
        menu_import.images = images
        menu_import.applied_at = timezone.now()
        menu_import.options = {**(menu_import.options or {}), "update_prices": update_prices}
        menu_import.save(update_fields=["status", "stats", "images", "applied_at", "options", "updated_at"])
        if images:
            from apps.delivery import tasks

            enqueue(tasks.import_images, str(menu_import.pk))
    try:
        from apps.audit.services import log_action

        log_action(
            "settings_update",
            restaurant=restaurant,
            user=by if getattr(by, "is_authenticated", False) else None,
            description=f"Menu imported from {menu_import.get_source_display()}",
            changes=stats,
        )
    except Exception:  # pragma: no cover - audit is best effort
        pass
    return stats


# ── images ────────────────────────────────────────────────────────────────

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def new_session():
    """Plain HTTP session for image downloads (tests swap this)."""
    import requests

    return requests.Session()


def download_images(menu_import: MenuImport, *, session=None) -> dict:
    """Fetch each queued image into MenuItem.image (the blurhash signal does the rest)."""
    from django.core.files.base import ContentFile

    if session is None:
        session = new_session()
    done = failed = 0
    for item_pk, url in menu_import.images or []:
        item = MenuItem.objects.filter(pk=item_pk, restaurant=menu_import.restaurant).first()
        if item is None or item.image:
            continue
        try:
            r = session.request("GET", url, timeout=20, headers={"User-Agent": "aimenu-menu-import/1.0"})
            content = getattr(r, "content", b"") or b""
            ctype = ""
            headers = getattr(r, "headers", None) or {}
            try:
                ctype = str(headers.get("Content-Type", "")).lower()
            except Exception:  # noqa: BLE001
                ctype = ""
            if (
                r.status_code >= 400
                or not content
                or len(content) > MAX_IMAGE_BYTES
                or ("image" not in ctype and ctype)
            ):
                failed += 1
                continue
            ext = ".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg"
            name = (urlparse(url).path.rsplit("/", 1)[-1] or f"{item_pk}{ext}")[-80:]
            if "." not in name:
                name += ext
            item.image.save(name, ContentFile(content), save=True)
            done += 1
        except Exception:  # noqa: BLE001
            logger.warning("Image download failed for %s (%s)", item_pk, url, exc_info=True)
            failed += 1
    menu_import.stats = {**(menu_import.stats or {}), "images_done": done, "images_failed": failed}
    menu_import.status = "imported"
    menu_import.save(update_fields=["stats", "status", "updated_at"])
    return {"done": done, "failed": failed}
