"""
Warehouse services -- the only place stock counters, lots and the ledger are
written.

Locking rule: every mutation locks the affected ``StockItem`` rows with
``select_for_update().order_by("pk")`` (same order everywhere, so two orders
touching overlapping ingredients cannot deadlock), updates the cached
counters with ``F()`` expressions under that lock and writes the ledger /
reservation rows in the same transaction.

Reservations: an order *reserves* its ingredients when placed (so other
customers cannot order beyond what is left), *consumes* them FIFO from lots
when the kitchen accepts it, and *releases* (or *restores*, if already
consumed) them when it is cancelled. Every transition is idempotent via
``OrderStockReservation.status``.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F, Prefetch, Q, Sum
from django.utils import timezone

from apps.inventory.exceptions import InsufficientStock, InventoryError, UnitMismatch
from apps.inventory.models import (
    COST,
    EmployeeMeal,
    InventoryAlert,
    InventoryAlertPlatformTask,
    OrderStockReservation,
    OrderStockReservationLine,
    RecipeLine,
    RestaurantDeliveryPlatform,
    StockAdjustment,
    StockItem,
    StockLot,
    StockMovement,
    UnitOfMeasure,
    WasteEntry,
    fmt_qty,
    money,
    q4,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0")

# Order statuses at which the kitchen has taken the order: reservations turn
# into consumption on the first move into any of these.
CONSUMING_STATUSES = {"confirmed", "preparing", "ready", "served", "completed"}
PRE_KITCHEN_STATUSES = {"pending_payment", "pending"}


def enabled(restaurant) -> bool:
    return bool(restaurant and restaurant.warehouse_enabled)


# ── helpers ───────────────────────────────────────────────────────────────


def _lock_items(item_ids) -> dict:
    """Row-lock the given stock items, always in pk order."""
    ids = list(item_ids)
    if not ids:
        return {}
    rows = StockItem.objects.select_for_update().filter(id__in=ids).order_by("pk")
    return {row.pk: row for row in rows}


def _bump(item: StockItem, *, on_hand=ZERO, reserved=ZERO):
    """Apply a counter delta with F() and mirror it on the in-memory row."""
    StockItem.objects.filter(pk=item.pk).update(
        on_hand_qty=F("on_hand_qty") + on_hand, reserved_qty=F("reserved_qty") + reserved
    )
    item.on_hand_qty = item.on_hand_qty + on_hand
    item.reserved_qty = item.reserved_qty + reserved


def _lots_fifo(item: StockItem):
    return (
        StockLot.objects.select_for_update()
        .filter(stock_item=item, remaining_qty__gt=0)
        .order_by(F("expiry_date").asc(nulls_last=True), "received_at", "pk")
    )


def _fallback_unit_cost(item: StockItem) -> Decimal:
    last = item.lots.order_by("-received_at", "-pk").values_list("unit_cost", flat=True).first()
    if last is not None:
        return last
    return item.default_unit_cost or ZERO


def _deduct_fifo(item: StockItem, qty: Decimal, *, kind, reason="", by=None, note="", lot=None, **links):
    """
    Take ``qty`` (base units, positive) out of the item's lots, oldest expiry
    first, writing one ledger row per lot touched. Anything the lots cannot
    cover becomes a lot-less row (stock goes negative rather than blocking
    the kitchen) and is reported back so the caller can raise an alert.

    Returns (movements, uncovered_qty).
    """
    remaining = q4(qty)
    movements = []
    lots = [lot] if lot is not None else list(_lots_fifo(item))
    if lot is not None:
        lot = StockLot.objects.select_for_update().get(pk=lot.pk)
        lots = [lot]
    for current in lots:
        if remaining <= 0:
            break
        take = min(current.remaining_qty, remaining)
        if take <= 0:
            continue
        current.remaining_qty = current.remaining_qty - take
        current.save(update_fields=["remaining_qty", "updated_at"])
        movements.append(
            StockMovement(
                restaurant_id=item.restaurant_id,
                stock_item=item,
                lot=current,
                kind=kind,
                reason=reason,
                quantity=-take,
                unit_cost=current.unit_cost,
                total_cost=money(take * current.unit_cost),
                staff_user=by,
                note=note,
                **links,
            )
        )
        remaining -= take
    uncovered = ZERO
    if remaining > 0:
        unit_cost = _fallback_unit_cost(item)
        movements.append(
            StockMovement(
                restaurant_id=item.restaurant_id,
                stock_item=item,
                lot=None,
                kind=kind,
                reason=reason,
                quantity=-remaining,
                unit_cost=unit_cost,
                total_cost=money(remaining * unit_cost),
                staff_user=by,
                note=(note + " " if note else "") + "(no lot coverage)",
                **links,
            )
        )
        uncovered = remaining
    StockMovement.objects.bulk_create(movements)
    _bump(item, on_hand=-q4(qty))
    return movements, uncovered


def _to_base(item: StockItem, qty, unit: UnitOfMeasure) -> Decimal:
    if unit.dimension != item.base_unit.dimension:
        raise UnitMismatch(f"{item.name} is tracked in {item.base_unit.code}; {unit.code} does not convert.")
    return UnitOfMeasure.convert(qty, unit, item.base_unit)


# ── recipes ───────────────────────────────────────────────────────────────


def recipe_lines_for(target) -> list[RecipeLine]:
    """Active-ingredient recipe lines of a MenuItem or Modifier (prefetch-aware)."""
    lines = target.recipe_lines.all()
    return [line for line in lines if line.stock_item.is_active]


def needs_for(target, portions=1) -> dict:
    """{stock_item_id: base qty} for ``portions`` of a dish or modifier."""
    need = defaultdict(Decimal)
    for line in recipe_lines_for(target):
        need[line.stock_item_id] += q4(line.qty_base * Decimal(portions))
    return dict(need)


def explode_order(order):
    """
    Ingredients an order needs.

    Returns ``(need_by_item, need_by_order_item)`` -- totals per stock item,
    and the same broken down per OrderItem so per-item cancellation can
    release just that dish. Cancelled order items and inactive stock items
    are skipped; dishes without a recipe need nothing.
    """
    items = (
        order.items.exclude(status="cancelled")
        .select_related("menu_item")
        .prefetch_related(
            Prefetch("menu_item__recipe_lines", queryset=RecipeLine.objects.select_related("stock_item", "unit")),
            Prefetch(
                "modifiers__modifier__recipe_lines", queryset=RecipeLine.objects.select_related("stock_item", "unit")
            ),
        )
    )
    return explode_order_items(items)


def explode_order_items(order_items):
    need_by_item = defaultdict(Decimal)
    need_by_order_item = {}
    for oi in order_items:
        per = defaultdict(Decimal)
        if oi.menu_item_id and oi.menu_item is not None:
            for sid, qty in needs_for(oi.menu_item, oi.quantity).items():
                per[sid] += qty
        for m in oi.modifiers.all():
            if m.modifier_id and m.modifier is not None:
                # OrderItemModifier has no quantity: one per portion.
                for sid, qty in needs_for(m.modifier, oi.quantity).items():
                    per[sid] += qty
        if per:
            need_by_order_item[oi.pk] = dict(per)
            for sid, qty in per.items():
                need_by_item[sid] += qty
    return dict(need_by_item), need_by_order_item


def available_portions(target) -> int | None:
    """How many portions the current stock covers; None when the dish is untracked."""
    lines = recipe_lines_for(target)
    if not lines:
        return None
    portions = None
    for line in lines:
        per = line.qty_base
        if per <= 0:
            continue
        n = int(line.stock_item.available_qty // per)
        portions = n if portions is None else min(portions, n)
    return max(portions or 0, 0)


def _shortfalls(order, need_by_item, items):
    """Explain a failed reservation per dish rather than per ingredient."""
    short_ids = {sid for sid, qty in need_by_item.items() if items[sid].available_qty < qty}
    out = []
    for oi in order.items.exclude(status="cancelled").select_related("menu_item"):
        if not oi.menu_item_id:
            continue
        lines = [line for line in recipe_lines_for(oi.menu_item) if line.stock_item_id in short_ids]
        if not lines:
            continue
        limiting = min(lines, key=lambda line: items[line.stock_item_id].available_qty / line.qty_base)
        out.append(
            {
                "menu_item_id": str(oi.menu_item_id),
                "name": oi.item_name,
                "requested": oi.quantity,
                "available_portions": max(int(items[limiting.stock_item_id].available_qty // limiting.qty_base), 0),
                "stock_item_name": items[limiting.stock_item_id].name,
            }
        )
    if not out:  # shortage only via modifiers
        for sid in short_ids:
            out.append(
                {
                    "menu_item_id": None,
                    "name": items[sid].name,
                    "requested": None,
                    "available_portions": 0,
                    "stock_item_name": items[sid].name,
                }
            )
    return out


# ── order lifecycle ───────────────────────────────────────────────────────


def reserve_for_order(order, *, strict=True) -> OrderStockReservation | None:
    """
    Hold the order's ingredients. Idempotent per order. Raises
    ``InsufficientStock`` when ``strict`` and the stock cannot cover it --
    inside the caller's transaction, so the order rolls back with it.
    """
    restaurant = order.restaurant
    if not enabled(restaurant):
        return None
    with transaction.atomic():
        res, created = OrderStockReservation.objects.select_for_update().get_or_create(
            order=order, defaults={"restaurant": restaurant, "strict": strict}
        )
        if not created:
            if res.status != OrderStockReservation.STATUS_RELEASED:
                return res
            # A hold the TTL job gave back (abandoned checkout that got paid
            # after all): arm it again. Rolls back with the caller if short.
            res.status = OrderStockReservation.STATUS_RESERVED
            res.released_at = None
            res.strict = strict
            res.save(update_fields=["status", "released_at", "strict", "updated_at"])
        need_by_item, need_by_order_item = explode_order(order)
        if not need_by_item:
            return res
        items = _lock_items(need_by_item)
        short = [sid for sid, qty in need_by_item.items() if items[sid].available_qty < qty]
        if short and strict:
            raise InsufficientStock(_shortfalls(order, need_by_item, items))
        if short:
            res.strict = False
            res.save(update_fields=["strict", "updated_at"])
        _add_reservation_lines(res, items, need_by_order_item)
        recompute_availability(restaurant.pk, list(need_by_item))
        return res


def _add_reservation_lines(res, items, need_by_order_item):
    lines = []
    for oi_id, per in need_by_order_item.items():
        for sid, qty in per.items():
            lines.append(
                OrderStockReservationLine(reservation=res, stock_item=items[sid], order_item_id=oi_id, quantity=qty)
            )
            _bump(items[sid], reserved=qty)
    OrderStockReservationLine.objects.bulk_create(lines)


def reserve_order_items(order, order_items, *, strict=True):
    """
    Extra dishes added to a live order. If the order is still waiting for
    the kitchen they are reserved (and may be refused); if it has already
    been accepted they are consumed straight away.
    """
    if not enabled(order.restaurant):
        return None
    with transaction.atomic():
        res, _ = OrderStockReservation.objects.select_for_update().get_or_create(
            order=order, defaults={"restaurant": order.restaurant, "strict": strict}
        )
        need_by_item, need_by_order_item = explode_order_items(
            order.items.filter(pk__in=[oi.pk for oi in order_items])
            .select_related("menu_item")
            .prefetch_related("menu_item__recipe_lines__stock_item", "modifiers__modifier__recipe_lines__stock_item")
        )
        if not need_by_item:
            return res
        items = _lock_items(need_by_item)
        if res.status == OrderStockReservation.STATUS_CONSUMED:
            for oi_id, per in need_by_order_item.items():
                for sid, qty in per.items():
                    _, uncovered = _deduct_fifo(items[sid], qty, kind="consume_sale", order=order, order_item_id=oi_id)
                    if uncovered:
                        _negative_stock_alert(items[sid])
        else:
            short = [sid for sid, qty in need_by_item.items() if items[sid].available_qty < qty]
            if short and strict:
                raise InsufficientStock(_shortfalls(order, need_by_item, items))
            _add_reservation_lines(res, items, need_by_order_item)
        recompute_availability(order.restaurant_id, list(need_by_item))
        return res


def release_reservation(order) -> bool:
    """Order cancelled before the kitchen took it: give the hold back."""
    with transaction.atomic():
        res = OrderStockReservation.objects.select_for_update().filter(order=order).first()
        if res is None or res.status != OrderStockReservation.STATUS_RESERVED:
            return False
        lines = list(res.lines.filter(is_active=True))
        items = _lock_items({line.stock_item_id for line in lines})
        for line in lines:
            _bump(items[line.stock_item_id], reserved=-line.quantity)
        res.lines.filter(is_active=True).update(is_active=False)
        res.status = OrderStockReservation.STATUS_RELEASED
        res.released_at = timezone.now()
        res.save(update_fields=["status", "released_at", "updated_at"])
        if items:
            recompute_availability(res.restaurant_id, list(items))
        return True


def consume_order(order, *, by=None) -> bool:
    """
    Kitchen accepted the order: turn the hold into FIFO lot consumption.
    A missing reservation (feature switched on after the order was placed,
    or a prepaid hold released by the TTL job) is created non-strictly first
    -- an accepted order is never refused.
    """
    if not enabled(order.restaurant):
        return False
    with transaction.atomic():
        res = OrderStockReservation.objects.select_for_update().filter(order=order).first()
        if res is None:
            res = reserve_for_order(order, strict=False)
            if res is None:
                return False
            res = OrderStockReservation.objects.select_for_update().get(pk=res.pk)
        if res.status != OrderStockReservation.STATUS_RESERVED:
            return False
        lines = list(res.lines.filter(is_active=True).order_by("pk"))
        items = _lock_items({line.stock_item_id for line in lines})
        for line in lines:
            item = items[line.stock_item_id]
            _bump(item, reserved=-line.quantity)
            _, uncovered = _deduct_fifo(
                item, line.quantity, kind="consume_sale", by=by, order=order, order_item_id=line.order_item_id
            )
            if uncovered:
                _negative_stock_alert(item)
        res.lines.filter(is_active=True).update(is_active=False)
        res.status = OrderStockReservation.STATUS_CONSUMED
        res.consumed_at = timezone.now()
        res.save(update_fields=["status", "consumed_at", "updated_at"])
        if items:
            recompute_availability(res.restaurant_id, list(items))
        return True


def restore_order(order, *, by=None) -> bool:
    """Order cancelled after consumption: put every consumed quantity back into its lot."""
    with transaction.atomic():
        res = OrderStockReservation.objects.select_for_update().filter(order=order).first()
        if res is None or res.status != OrderStockReservation.STATUS_CONSUMED:
            return False
        _restore_movements(order, StockMovement.objects.filter(order=order, kind="consume_sale"), by=by)
        res.status = OrderStockReservation.STATUS_RESTORED
        res.released_at = timezone.now()
        res.save(update_fields=["status", "released_at", "updated_at"])
        return True


def _restore_movements(order, movements, *, by=None):
    movements = list(movements.select_related("lot"))
    items = _lock_items({m.stock_item_id for m in movements})
    restored = []
    for m in movements:
        qty = -m.quantity
        if m.lot_id:
            StockLot.objects.filter(pk=m.lot_id).update(remaining_qty=F("remaining_qty") + qty)
        restored.append(
            StockMovement(
                restaurant_id=m.restaurant_id,
                stock_item_id=m.stock_item_id,
                lot_id=m.lot_id,
                kind="restore_sale",
                quantity=qty,
                unit_cost=m.unit_cost,
                total_cost=m.total_cost,
                order=order,
                order_item_id=m.order_item_id,
                staff_user=by,
            )
        )
        _bump(items[m.stock_item_id], on_hand=qty)
    StockMovement.objects.bulk_create(restored)
    if items:
        recompute_availability(order.restaurant_id, list(items))


def release_order_item(order_item, *, by=None) -> bool:
    """One dish cancelled: release (or restore) just its share."""
    order = order_item.order
    if not enabled(order.restaurant):
        return False
    with transaction.atomic():
        res = OrderStockReservation.objects.select_for_update().filter(order=order).first()
        if res is None:
            return False
        if res.status == OrderStockReservation.STATUS_RESERVED:
            lines = list(res.lines.filter(is_active=True, order_item=order_item))
            items = _lock_items({line.stock_item_id for line in lines})
            for line in lines:
                _bump(items[line.stock_item_id], reserved=-line.quantity)
            res.lines.filter(is_active=True, order_item=order_item).update(is_active=False)
            if items:
                recompute_availability(order.restaurant_id, list(items))
            return bool(lines)
        if res.status == OrderStockReservation.STATUS_CONSUMED:
            movements = StockMovement.objects.filter(order=order, order_item=order_item, kind="consume_sale")
            if not StockMovement.objects.filter(order=order, order_item=order_item, kind="restore_sale").exists():
                _restore_movements(order, movements, by=by)
                return True
        return False


# ── stock operations ──────────────────────────────────────────────────────


def receive_stock(
    stock_item: StockItem,
    qty,
    unit: UnitOfMeasure,
    *,
    unit_cost=None,
    total_cost=None,
    expiry_date=None,
    supplier_name="",
    reference="",
    received_at=None,
    notes="",
    by=None,
    lot: StockLot | None = None,
) -> StockLot:
    """
    Book a delivery as a new lot. ``qty`` is in ``unit``; the cost may be
    given per ``unit`` (``unit_cost``) or for the whole delivery
    (``total_cost``). Pass ``lot`` to fill an unsaved instance (admin form).
    """
    qty = Decimal(qty)
    if qty <= 0:
        raise InventoryError({"quantity": "Quantity must be positive."})
    if not stock_item.is_active:
        raise InventoryError({"stock_item": f"{stock_item.name} is inactive."})
    qty_base = _to_base(stock_item, qty, unit)
    if total_cost is not None and total_cost != "":
        total = money(total_cost)
        cost_per_base = (total / qty_base).quantize(COST) if qty_base else ZERO
    elif unit_cost is not None and unit_cost != "":
        # unit_cost is per *entered* unit (e.g. per kg); one entered unit is
        # unit.factor/base.factor base units, so divide by that.
        cost_per_base = (Decimal(unit_cost) * stock_item.base_unit.factor_to_base / unit.factor_to_base).quantize(COST)
        total = money(cost_per_base * qty_base)
    else:
        cost_per_base = stock_item.default_unit_cost or ZERO
        total = money(cost_per_base * qty_base)
    with transaction.atomic():
        item = _lock_items([stock_item.pk])[stock_item.pk]
        lot = lot or StockLot()
        lot.stock_item = item
        lot.restaurant_id = item.restaurant_id
        lot.received_qty = qty_base
        lot.remaining_qty = qty_base
        lot.unit_cost = cost_per_base
        lot.total_cost = total
        lot.expiry_date = expiry_date
        lot.received_at = received_at or timezone.now()
        lot.supplier_name = supplier_name or item.supplier_name
        lot.reference = reference or ""
        lot.received_by = by
        lot.notes = notes or ""
        lot.save()
        StockMovement.objects.create(
            restaurant_id=item.restaurant_id,
            stock_item=item,
            lot=lot,
            kind="receive",
            quantity=qty_base,
            unit_cost=cost_per_base,
            total_cost=total,
            staff_user=by,
            note=f"{qty} {unit.code}" + (f" · {reference}" if reference else ""),
        )
        _bump(item, on_hand=qty_base)
        if item.default_unit_cost is None:
            StockItem.objects.filter(pk=item.pk).update(default_unit_cost=cost_per_base)
        _resolve_lot_alerts(item)
        recompute_availability(item.restaurant_id, [item.pk])
    _audit("stock_receive", item.restaurant_id, by, f"Received {qty} {unit.code} of {item.name}", lot)
    from apps.fiscal import hooks as fiscal_hooks

    fiscal_hooks.on_stock_received(lot)
    stock_item.on_hand_qty = item.on_hand_qty
    return lot


def _resolve_lot_alerts(item):
    for key in (f"negative_stock:stock_item:{item.pk}",):
        _resolve_alert(item.restaurant_id, key)


def record_waste(entry: WasteEntry, *, by=None) -> WasteEntry:
    """Save a waste/spoilage/mistake entry and write it off (specific lot, else FIFO)."""
    entry.full_clean(exclude=["restaurant", "reported_by"])
    item = entry.stock_item
    qty_base = _to_base(item, entry.quantity, entry.unit)
    with transaction.atomic():
        item = _lock_items([item.pk])[item.pk]
        entry.restaurant_id = item.restaurant_id
        entry.save()
        movements, _ = _deduct_fifo(
            item,
            qty_base,
            kind="waste",
            reason=entry.reason,
            by=by,
            note=entry.note,
            lot=entry.lot,
            waste_entry=entry,
        )
        entry.total_cost = money(sum(m.total_cost for m in movements))
        entry.save(update_fields=["total_cost", "updated_at"])
        if entry.lot_id and entry.reason == "expired":
            StockLot.objects.filter(pk=entry.lot_id, remaining_qty__lte=0).update(is_expired_out=True)
            _resolve_alert(item.restaurant_id, f"expiring_soon:lot:{entry.lot_id}")
            _resolve_alert(item.restaurant_id, f"expired:lot:{entry.lot_id}")
        recompute_availability(item.restaurant_id, [item.pk])
    _audit(
        "stock_waste",
        item.restaurant_id,
        by,
        f"{entry.get_reason_display()}: {entry.quantity} {entry.unit} {item.name}",
        entry,
    )
    return entry


def record_expired_lot(lot: StockLot, *, by=None, staff_member=None) -> WasteEntry | None:
    """Write off whatever is left of an expired lot."""
    if lot.remaining_qty <= 0:
        StockLot.objects.filter(pk=lot.pk).update(is_expired_out=True)
        return None
    entry = WasteEntry(
        restaurant_id=lot.restaurant_id,
        stock_item=lot.stock_item,
        lot=lot,
        quantity=lot.remaining_qty,
        unit=lot.stock_item.base_unit,
        reason="expired",
        note=f"Expired {lot.expiry_date}",
        reported_by=staff_member,
        occurred_on=timezone.localdate(),
    )
    return record_waste(entry, by=by)


def record_employee_meal(meal: EmployeeMeal, *, by=None) -> EmployeeMeal:
    """
    Save the meal and deduct what was eaten: a dish explodes through its
    recipe, a raw item is deducted directly. Meals never go through the
    reservation step and may push stock negative -- it has been eaten.
    """
    meal.full_clean(exclude=["restaurant", "recorded_by"])
    with transaction.atomic():
        if meal.menu_item_id:
            need = needs_for(meal.menu_item, meal.quantity)
            if not need:
                raise InventoryError({"menu_item": f"{meal.menu_item} has no recipe, so nothing can be deducted."})
        else:
            need = {meal.stock_item_id: _to_base(meal.stock_item, meal.quantity, meal.unit)}
        items = _lock_items(need)
        meal.restaurant_id = next(iter(items.values())).restaurant_id
        meal.recorded_by = by or meal.recorded_by
        meal.save()
        total = ZERO
        for sid, qty in need.items():
            movements, _ = _deduct_fifo(
                items[sid], qty, kind="employee_meal", by=by, note=meal.note, employee_meal=meal
            )
            total += sum(m.total_cost for m in movements)
        meal.total_cost = money(total)
        meal.save(update_fields=["total_cost", "updated_at"])
        recompute_availability(meal.restaurant_id, list(items))
    _audit("employee_meal", meal.restaurant_id, by, str(meal), meal)
    return meal


def apply_adjustment(adj: StockAdjustment, *, by=None) -> StockAdjustment:
    """
    Stock count ("I counted X") or correction ("+/- X"). Positive deltas
    create a synthetic lot at the current cost so FIFO has something to eat;
    negative deltas are written off oldest-first.
    """
    adj.full_clean(exclude=["restaurant", "made_by"])
    item = adj.stock_item
    entered_base = _to_base(item, adj.quantity, adj.unit)
    with transaction.atomic():
        item = _lock_items([item.pk])[item.pk]
        delta = entered_base - item.on_hand_qty if adj.mode == "count" else entered_base
        adj.restaurant_id = item.restaurant_id
        adj.made_by = by or adj.made_by
        adj.delta_base = delta
        adj.save()
        if delta > 0:
            unit_cost = item.current_unit_cost() or ZERO
            lot = StockLot.objects.create(
                stock_item=item,
                restaurant_id=item.restaurant_id,
                received_qty=delta,
                remaining_qty=delta,
                unit_cost=unit_cost,
                total_cost=money(delta * unit_cost),
                reference="adjustment",
                received_by=by,
                notes=adj.note,
            )
            StockMovement.objects.create(
                restaurant_id=item.restaurant_id,
                stock_item=item,
                lot=lot,
                kind="adjustment",
                reason=adj.reason,
                quantity=delta,
                unit_cost=unit_cost,
                total_cost=money(delta * unit_cost),
                staff_user=by,
                note=adj.note,
                adjustment=adj,
            )
            _bump(item, on_hand=delta)
        elif delta < 0:
            _deduct_fifo(item, -delta, kind="adjustment", reason=adj.reason, by=by, note=adj.note, adjustment=adj)
        if item.on_hand_qty >= 0:
            _resolve_alert(item.restaurant_id, f"negative_stock:stock_item:{item.pk}")
        recompute_availability(item.restaurant_id, [item.pk])
    _audit("stock_adjust", item.restaurant_id, by, f"{adj.get_mode_display()} {item.name}: {adj.delta_base:+}", adj)
    return adj


# ── availability & alerts ─────────────────────────────────────────────────


def _dedupe_key(kind, **target):
    for name in ("menu_item", "modifier", "stock_item", "lot"):
        obj = target.get(name)
        if obj is not None:
            return f"{kind}:{name}:{getattr(obj, 'pk', obj)}"
    return f"{kind}:restaurant"


def open_alert(restaurant_id, kind, message, *, payload=None, **target) -> tuple[InventoryAlert, bool]:
    key = _dedupe_key(kind, **target)
    existing = InventoryAlert.objects.filter(restaurant_id=restaurant_id, dedupe_key=key, status="open").first()
    if existing:
        return existing, False
    fields = {k: v for k, v in target.items() if v is not None}
    try:
        with transaction.atomic():
            alert = InventoryAlert.objects.create(
                restaurant_id=restaurant_id,
                kind=kind,
                message=message[:300],
                payload=payload or {},
                dedupe_key=key,
                **fields,
            )
        from apps.notifications import hooks as notification_hooks

        notification_hooks.on_inventory_alert(alert)
        return alert, True
    except IntegrityError:
        return InventoryAlert.objects.get(restaurant_id=restaurant_id, dedupe_key=key, status="open"), False


def _resolve_alert(restaurant_id, key, *, by=None):
    for alert in InventoryAlert.objects.filter(restaurant_id=restaurant_id, dedupe_key=key, status="open"):
        alert.resolve(by=by, reason="auto")


def _negative_stock_alert(item):
    open_alert(
        item.restaurant_id,
        "negative_stock",
        f"{item.name} went negative ({fmt_qty(item.available_qty, item.base_unit)}) -- do a stock count.",
        stock_item=item,
        payload={"available": str(item.available_qty)},
    )


def _platform_tasks(alert: InventoryAlert, menu_item, available: bool):
    """One checklist row per enabled delivery platform, then ask the adapter."""
    from apps.inventory.platforms import get_adapter

    for link in RestaurantDeliveryPlatform.objects.filter(restaurant_id=alert.restaurant_id, is_enabled=True):
        task, _ = InventoryAlertPlatformTask.objects.get_or_create(
            alert=alert, platform=link, defaults={"action": "enable" if available else "disable"}
        )
        try:
            result = get_adapter(link).set_item_availability(link, menu_item, available, task=task)
        except Exception:  # never let a platform call break stock accounting
            logger.exception("Delivery platform adapter failed for %s", link)
            continue
        if result.ok and not result.manual:
            task.adapter_status = "sent"
            task.is_done = True
            task.done_at = timezone.now()
            task.save(update_fields=["adapter_status", "is_done", "done_at", "updated_at"])
        elif not result.ok:
            task.adapter_status = "failed"
            task.adapter_error = result.error or ""
            task.save(update_fields=["adapter_status", "adapter_error", "updated_at"])


def _name(target):
    return target.safe_translation_getter("name", any_language=True) or str(target)


def recompute_availability(restaurant_id, stock_item_ids=None):
    """
    Flip ``auto_disabled_by_stock`` on every dish / modifier whose recipe
    touches the given stock items, and raise / resolve the matching alerts.
    Cheap enough to run inline after every mutation.
    """
    from apps.menu.models import MenuItem, Modifier

    items = StockItem.objects.filter(restaurant_id=restaurant_id, is_active=True)
    if stock_item_ids is not None:
        items = items.filter(pk__in=list(stock_item_ids))
    avail = {i.pk: i.available_qty for i in items}
    if not avail:
        return
    line_qs = RecipeLine.objects.select_related("stock_item", "unit")

    def _short(target):
        for line in target.recipe_lines.all():
            si = line.stock_item
            if not si.is_active:
                continue
            available = avail.get(si.pk, si.available_qty)
            if available < line.qty_base:
                return si
        return None

    menu_items = (
        MenuItem.objects.filter(restaurant_id=restaurant_id, recipe_lines__stock_item__in=list(avail))
        .distinct()
        .prefetch_related(Prefetch("recipe_lines", queryset=line_qs))
    )
    for mi in menu_items:
        short = _short(mi)
        if short and not mi.auto_disabled_by_stock:
            MenuItem.objects.filter(pk=mi.pk).update(auto_disabled_by_stock=True)
            mi.auto_disabled_by_stock = True
            _resolve_alert(restaurant_id, f"back_in_stock:menu_item:{mi.pk}")
            alert, created = open_alert(
                restaurant_id,
                "out_of_stock",
                f"{_name(mi)} is sold out ({short.name} ran out).",
                menu_item=mi,
                payload={"stock_item": str(short.pk), "stock_item_name": short.name},
            )
            if created:
                _platform_tasks(alert, mi, available=False)
        elif not short and mi.auto_disabled_by_stock:
            MenuItem.objects.filter(pk=mi.pk).update(auto_disabled_by_stock=False)
            mi.auto_disabled_by_stock = False
            _resolve_alert(restaurant_id, f"out_of_stock:menu_item:{mi.pk}")
            alert, created = open_alert(restaurant_id, "back_in_stock", f"{_name(mi)} is back in stock.", menu_item=mi)
            if created:
                _platform_tasks(alert, mi, available=True)

    modifiers = (
        Modifier.objects.filter(group__restaurant_id=restaurant_id, recipe_lines__stock_item__in=list(avail))
        .distinct()
        .prefetch_related(Prefetch("recipe_lines", queryset=line_qs))
    )
    for mod in modifiers:
        short = _short(mod)
        if short and not mod.auto_disabled_by_stock:
            Modifier.objects.filter(pk=mod.pk).update(auto_disabled_by_stock=True)
            _resolve_alert(restaurant_id, f"back_in_stock:modifier:{mod.pk}")
            open_alert(
                restaurant_id,
                "out_of_stock",
                f"Option {_name(mod)} is sold out ({short.name} ran out).",
                modifier=mod,
                payload={"stock_item": str(short.pk), "stock_item_name": short.name},
            )
        elif not short and mod.auto_disabled_by_stock:
            Modifier.objects.filter(pk=mod.pk).update(auto_disabled_by_stock=False)
            _resolve_alert(restaurant_id, f"out_of_stock:modifier:{mod.pk}")
            open_alert(restaurant_id, "back_in_stock", f"Option {_name(mod)} is back in stock.", modifier=mod)

    for item in items:
        if item.available_qty <= item.min_level:
            open_alert(
                restaurant_id,
                "low_stock",
                f"{item.name} is low: {fmt_qty(item.available_qty, item.base_unit)} left (min {fmt_qty(item.min_level)}).",
                stock_item=item,
                payload={"available": str(item.available_qty), "min_level": str(item.min_level)},
            )
        else:
            _resolve_alert(restaurant_id, f"low_stock:stock_item:{item.pk}")


def on_feature_toggled(restaurant, is_enabled: bool, *, by=None):
    """Off: forget every automatic decision. On: take stock of everything."""
    from apps.menu.models import MenuItem, Modifier

    if is_enabled:
        recompute_availability(restaurant.pk)
    else:
        MenuItem.objects.filter(restaurant=restaurant, auto_disabled_by_stock=True).update(auto_disabled_by_stock=False)
        Modifier.objects.filter(group__restaurant=restaurant, auto_disabled_by_stock=True).update(
            auto_disabled_by_stock=False
        )
        for alert in InventoryAlert.objects.filter(restaurant=restaurant, status="open"):
            alert.resolve(by=by, reason="auto")
    _audit("warehouse_toggle", restaurant.pk, by, f"Warehouse {'enabled' if is_enabled else 'disabled'}", restaurant)


# ── reports ───────────────────────────────────────────────────────────────


def low_stock_report(restaurant):
    return [
        i
        for i in StockItem.objects.filter(restaurant=restaurant, is_active=True).select_related("base_unit")
        if i.available_qty <= i.min_level
    ]


def expiring_report(restaurant, within_days=None):
    """Open lots expiring within ``within_days`` (default: each item's own warning window)."""
    today = timezone.localdate()
    lots = (
        StockLot.objects.filter(restaurant=restaurant, remaining_qty__gt=0, expiry_date__isnull=False)
        .select_related("stock_item", "stock_item__base_unit")
        .order_by("expiry_date")
    )
    out = []
    for lot in lots:
        window = within_days if within_days is not None else lot.stock_item.expiry_warning_days
        if (lot.expiry_date - today).days <= window:
            out.append(lot)
    return out


@dataclass
class BuyLine:
    stock_item: StockItem
    available: Decimal
    expiring: Decimal
    suggested_base: Decimal
    purchase_unit: UnitOfMeasure
    suggested_purchase: Decimal
    packs: int | None
    unit_cost: Decimal
    estimated_cost: Decimal
    reasons: list = field(default_factory=list)


def buy_list(restaurant, for_date=None) -> list[BuyLine]:
    """
    What to buy so every item is back at its par level on ``for_date``:
    par - available, plus whatever will expire before then.
    """
    for_date = for_date or (timezone.localdate() + timezone.timedelta(days=1))
    expiring = (
        StockLot.objects.filter(restaurant=restaurant, remaining_qty__gt=0, expiry_date__lt=for_date)
        .values("stock_item_id")
        .annotate(qty=Sum("remaining_qty"))
    )
    expiring = {row["stock_item_id"]: row["qty"] for row in expiring}
    lines = []
    for item in StockItem.objects.filter(restaurant=restaurant, is_active=True).select_related(
        "base_unit", "purchase_unit"
    ):
        available = item.available_qty
        lost = expiring.get(item.pk, ZERO)
        need = q4(item.par_level - (available - lost))
        reasons = []
        if available <= 0:
            reasons.append("out")
        elif available <= item.min_level:
            reasons.append("low")
        if lost:
            reasons.append("expiring")
        if need <= 0:
            continue
        if not reasons:
            reasons.append("below_par")
        purchase_unit = item.purchase_unit or item.base_unit
        suggested_purchase = UnitOfMeasure.convert(need, item.base_unit, purchase_unit)
        packs = None
        if item.purchase_unit_id and item.purchase_pack_qty:
            packs = math.ceil(suggested_purchase / item.purchase_pack_qty)
            suggested_purchase = q4(Decimal(packs) * item.purchase_pack_qty)
            need = purchase_unit.to_base(suggested_purchase)
        unit_cost = item.current_unit_cost() or ZERO
        lines.append(
            BuyLine(
                stock_item=item,
                available=available,
                expiring=lost,
                suggested_base=need,
                purchase_unit=purchase_unit,
                suggested_purchase=suggested_purchase,
                packs=packs,
                unit_cost=unit_cost,
                estimated_cost=money(need * unit_cost),
                reasons=reasons,
            )
        )
    lines.sort(key=lambda line: (0 if "out" in line.reasons else 1, line.stock_item.name.lower()))
    return lines


def recipe_cost(target) -> Decimal | None:
    """Ingredient cost of one portion at current lot prices; None when untracked."""
    lines = list(target.recipe_lines.select_related("stock_item", "unit"))
    if not lines:
        return None
    total = ZERO
    for line in lines:
        unit_cost = line.stock_item.current_unit_cost() or ZERO
        total += line.qty_base * unit_cost
    return money(total)


def line_cost(line: RecipeLine) -> Decimal:
    return money(line.qty_base * (line.stock_item.current_unit_cost() or ZERO))


def order_cost(order) -> Decimal:
    agg = StockMovement.objects.filter(order=order, kind__in=["consume_sale", "restore_sale"]).aggregate(
        consumed=Sum("total_cost", filter=Q(kind="consume_sale")),
        restored=Sum("total_cost", filter=Q(kind="restore_sale")),
    )
    return money((agg["consumed"] or ZERO) - (agg["restored"] or ZERO))


def rebuild_stock_cache(restaurant=None) -> list[dict]:
    """Recompute on_hand/reserved from the ledger + active reservation lines; returns the drift found."""
    qs = StockItem.objects.all()
    if restaurant is not None:
        qs = qs.filter(restaurant=restaurant)
    drift = []
    with transaction.atomic():
        for item in qs.select_for_update().order_by("pk"):
            on_hand = item.movements.aggregate(t=Sum("quantity"))["t"] or ZERO
            reserved = item.reservation_lines.filter(is_active=True).aggregate(t=Sum("quantity"))["t"] or ZERO
            if q4(on_hand) != item.on_hand_qty or q4(reserved) != item.reserved_qty:
                drift.append(
                    {
                        "stock_item": item,
                        "on_hand": (item.on_hand_qty, q4(on_hand)),
                        "reserved": (item.reserved_qty, q4(reserved)),
                    }
                )
                StockItem.objects.filter(pk=item.pk).update(on_hand_qty=q4(on_hand), reserved_qty=q4(reserved))
    return drift


# ── audit ─────────────────────────────────────────────────────────────────


def _audit(action, restaurant_id, user, description, target=None):
    try:
        from apps.audit.services import log_action
        from apps.tenants.models import Restaurant

        log_action(
            action,
            user=user if getattr(user, "is_authenticated", False) else None,
            restaurant=Restaurant.objects.filter(pk=restaurant_id).first(),
            description=description,
            target_model=type(target).__name__ if target is not None else "",
            target_id=str(getattr(target, "pk", "")) if target is not None else "",
        )
    except Exception:  # pragma: no cover - auditing must never break stock work
        logger.exception("inventory audit log failed")
