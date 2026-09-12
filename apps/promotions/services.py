"""
Automatic promotions (happy hour) on order lines, promo codes as an
order-level discount, '86 today' and combo helpers. Everything money-related
ends in ``order.calculate_totals()`` so receipts and reports agree.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils.translation import gettext as _

from apps.orders.models import OrderDiscount
from apps.promotions.availability import end_of_day, local_now
from apps.promotions.models import Promotion, PromotionUse

ZERO = Decimal("0")


class PromotionError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "promotions_enabled", False))


# ── happy hours ───────────────────────────────────────────────────────────


def live_happy_hours(restaurant, *, now=None, channel: str = "") -> list[Promotion]:
    if not enabled(restaurant):
        return []
    local = local_now(restaurant, now)
    rows = Promotion.objects.filter(restaurant=restaurant, kind="happy_hour", is_active=True).select_related("schedule")
    return [p for p in rows if p.is_live(local, channel=channel)]


def _targets(promo):
    return (
        set(promo.categories.values_list("pk", flat=True)) if promo.applies_to == "categories" else set(),
        set(promo.items.values_list("pk", flat=True)) if promo.applies_to == "items" else set(),
    )


def promo_prices(restaurant, items, *, now=None, channel: str = "") -> dict:
    """{menu_item_id: (promo_price, promotion)} for the dishes a live happy hour touches."""
    out = {}
    promos = live_happy_hours(restaurant, now=now, channel=channel)
    if not promos:
        return out
    scoped = [(p, *_targets(p)) for p in promos]
    for item in items:
        best = None
        for p, cats, ids in scoped:
            if not p.applies_to_item(item, category_ids=cats, item_ids=ids):
                continue
            price = (Decimal(item.price) - p.discount_for(item.price)).quantize(Decimal("0.01"))
            if best is None or price < best[0]:
                best = (max(price, ZERO), p)
        if best is not None and best[0] < item.price:
            out[item.pk] = best
    return out


def apply_automatic(order, *, channel: str = "", now=None) -> list:
    """Apply the best live happy hour to each eligible line (never on top of a manual discount / comp)."""
    restaurant = order.restaurant
    promos = live_happy_hours(restaurant, now=now, channel=channel)
    if not promos:
        return []
    scoped = [(p, *_targets(p)) for p in promos]
    touched = []
    used: dict = {}
    with transaction.atomic():
        for line in order.items.exclude(status="cancelled").select_related("menu_item"):
            if line.menu_item_id is None or line.is_comped:
                continue
            if line.discount_amount and not line.promotion_id:
                continue  # a human discount stays
            best = None
            for p, cats, ids in scoped:
                if not p.applies_to_item(line.menu_item, category_ids=cats, item_ids=ids):
                    continue
                off = p.discount_for(line.total_price)
                if off > ZERO and (best is None or off > best[0]):
                    best = (off, p)
            if best is None:
                if line.promotion_id:  # promotion no longer live -> drop it
                    line.discount_amount = ZERO
                    line.discount_reason_text = ""
                    line.promotion = None
                    line.save(update_fields=["discount_amount", "discount_reason_text", "promotion", "updated_at"])
                continue
            off, promo = best
            line.discount_amount = off
            line.discount_reason_text = promo.name[:200]
            line.promotion = promo
            line.save(update_fields=["discount_amount", "discount_reason_text", "promotion", "updated_at"])
            used[promo.pk] = used.get(promo.pk, ZERO) + off
            touched.append(line)
        for promo, _cats, _ids in scoped:
            amount = used.get(promo.pk)
            if amount is None:
                continue
            _, created = PromotionUse.objects.update_or_create(
                promotion=promo,
                order=order,
                defaults={"customer": order.customer, "phone": order.customer_phone or "", "amount": amount},
            )
            if created:
                Promotion.objects.filter(pk=promo.pk).update(uses_count=F("uses_count") + 1)
        order.calculate_totals()
    return touched


# ── promo codes ───────────────────────────────────────────────────────────


def find_code(restaurant, code: str) -> Promotion | None:
    code = (code or "").strip().upper()
    if not code:
        return None
    return (
        Promotion.objects.filter(restaurant=restaurant, kind="promo_code", code=code).select_related("schedule").first()
    )


def _eligible_base(order, promo) -> Decimal:
    cats, ids = _targets(promo)
    base = ZERO
    for line in order.items.exclude(status="cancelled").select_related("menu_item"):
        if line.menu_item is None:
            if promo.applies_to == "all":
                base += line.net_price
            continue
        if promo.applies_to_item(line.menu_item, category_ids=cats, item_ids=ids):
            base += line.net_price
    return base


def check_code(restaurant, code: str, *, order=None, customer=None, phone: str = "", channel: str = "", now=None):
    """Validate a code; returns (promotion, discount_amount_preview). Raises PromotionError."""
    if not enabled(restaurant):
        raise PromotionError("promotions_off", _("Promotions are not enabled."))
    promo = find_code(restaurant, code)
    if promo is None:
        raise PromotionError("invalid_code", _("This code is not valid."))
    local = local_now(restaurant, now)
    if not promo.is_active or (promo.starts_on and local.date() < promo.starts_on):
        raise PromotionError("invalid_code", _("This code is not valid."))
    if promo.ends_on and local.date() > promo.ends_on:
        raise PromotionError("expired", _("This code has expired."))
    if promo.max_uses and promo.uses_count >= promo.max_uses:
        raise PromotionError("exhausted", _("This code has been used up."))
    if channel and promo.channels and channel not in promo.channels:
        raise PromotionError("wrong_channel", _("This code cannot be used here."))
    if promo.schedule_id and not promo.schedule.matches(local):
        raise PromotionError(
            "outside_schedule", _("This code only works during %(window)s.") % {"window": promo.schedule.label()}
        )
    if promo.max_uses_per_customer:
        uses = PromotionUse.objects.filter(promotion=promo)
        if customer is not None and getattr(customer, "is_authenticated", False):
            uses = uses.filter(customer=customer)
        elif phone:
            uses = uses.filter(phone=phone)
        else:
            uses = uses.none()
        if uses.count() >= promo.max_uses_per_customer:
            raise PromotionError("already_used", _("You have already used this code."))
    preview = ZERO
    if order is not None:
        if promo.min_order_amount and order.subtotal - order.discount_amount < promo.min_order_amount:
            raise PromotionError(
                "min_order",
                _("Add %(amount)s more to use this code.") % {"amount": promo.min_order_amount},
            )
        if not promo.stackable and (
            order.discounts.filter(kind="promo").exists() or order.items.filter(promotion__isnull=False).exists()
        ):
            raise PromotionError("not_stackable", _("This code cannot be combined with another promotion."))
        preview = promo.discount_for(_eligible_base(order, promo))
        if preview <= ZERO:
            raise PromotionError("nothing_eligible", _("Nothing in this order is eligible for the code."))
    return promo, preview


def redeem_code(order, code: str, *, by=None, channel: str = "", now=None) -> OrderDiscount:
    from apps.orders.services import _open_for_money_changes, _refuse_if_paid

    _open_for_money_changes(order)
    _refuse_if_paid(order)
    customer = order.customer or (by if getattr(by, "is_authenticated", False) and not _is_staff(by, order) else None)
    promo, amount = check_code(
        order.restaurant, code, order=order, customer=customer, phone=order.customer_phone, channel=channel, now=now
    )
    with transaction.atomic():
        if order.discounts.filter(promotion=promo).exists():
            raise PromotionError("already_applied", _("This code is already applied."))
        discount = OrderDiscount.objects.create(
            order=order,
            kind="promo",
            mode="fixed",
            value=amount,
            reason_text=f"{promo.name} ({promo.code})"[:200],
            applied_by=by if getattr(by, "is_authenticated", False) else None,
            promotion=promo,
        )
        PromotionUse.objects.update_or_create(
            promotion=promo,
            order=order,
            defaults={"customer": order.customer, "phone": order.customer_phone or "", "amount": amount},
        )
        Promotion.objects.filter(pk=promo.pk).update(uses_count=F("uses_count") + 1)
        order.calculate_totals()
        discount.refresh_from_db(fields=["amount"])
    return discount


def remove_code(order, *, by=None) -> int:
    from apps.orders.services import _open_for_money_changes, _refuse_if_paid

    _open_for_money_changes(order)
    _refuse_if_paid(order)
    with transaction.atomic():
        rows = list(order.discounts.filter(kind="promo", promotion__isnull=False))
        for d in rows:
            PromotionUse.objects.filter(promotion=d.promotion, order=order).delete()
            Promotion.objects.filter(pk=d.promotion_id, uses_count__gt=0).update(uses_count=F("uses_count") - 1)
            d.delete()
        order.calculate_totals()
    return len(rows)


def _is_staff(user, order) -> bool:
    return user.staff_memberships.filter(restaurant=order.restaurant, is_active=True).exists() or (
        order.restaurant.owner_id == user.pk
    )


# ── 86 today ──────────────────────────────────────────────────────────────


def set_unavailable(item, until, *, by=None):
    """``until``: 'today' (until midnight), 'tomorrow' (until tomorrow midnight), an aware datetime, or None to clear."""
    if until == "today":
        value = end_of_day(item.restaurant)
    elif until == "tomorrow":
        from datetime import timedelta

        value = end_of_day(item.restaurant) + timedelta(days=1)
    else:
        value = until
    item.unavailable_until = value
    item.save(update_fields=["unavailable_until", "updated_at"])
    try:
        from apps.audit.services import log_action

        log_action(
            "menu_availability",
            restaurant=item.restaurant,
            user=by if getattr(by, "is_authenticated", False) else None,
            description=f"{item} {'off until ' + value.isoformat() if value else 'back on the menu'}",
            target_model="menuitem",
            target_id=str(item.pk),
        )
    except Exception:  # pragma: no cover
        pass
    return item


# ── reporting ─────────────────────────────────────────────────────────────


def usage_report(restaurant, start, end) -> list[dict]:
    from django.db.models import Count, Sum

    rows = (
        PromotionUse.objects.filter(promotion__restaurant=restaurant, created_at__gte=start, created_at__lt=end)
        .values("promotion__name", "promotion__kind", "promotion__code")
        .annotate(uses=Count("id"), discount=Sum("amount"))
        .order_by("-discount")
    )
    return [
        {
            "name": r["promotion__name"],
            "kind": r["promotion__kind"],
            "code": r["promotion__code"],
            "uses": r["uses"],
            "discount": r["discount"] or ZERO,
        }
        for r in rows
    ]


def combo_needs(item, portions=1) -> dict:
    """Ingredient needs of a combo = sum of its components' recipes (used by the warehouse)."""
    from apps.inventory.services import needs_for

    need: dict = {}
    for comp in item.combo_components.select_related("item"):
        for sid, qty in needs_for(comp.item, portions * comp.quantity).items():
            need[sid] = need.get(sid, ZERO) + qty
    return need
