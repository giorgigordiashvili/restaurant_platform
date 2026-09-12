"""
Purchase orders: build from the buy list, send to the supplier, receive
against the order (which books stock lots and updates supplier prices).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.inventory.models import StockItem, StockLot
from apps.purchasing.models import PriceObservation, PurchaseOrder, PurchaseOrderLine, Supplier, SupplierItem

logger = logging.getLogger(__name__)
ZERO = Decimal("0")


class PurchasingError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "purchasing_enabled", False))


# ── suppliers ─────────────────────────────────────────────────────────────


def backfill_suppliers(restaurant) -> int:
    """Turn free-text supplier names on stock items into Supplier rows (idempotent)."""
    created = 0
    names = (
        StockItem.objects.filter(restaurant=restaurant)
        .exclude(supplier_name="")
        .values_list("supplier_name", flat=True)
        .distinct()
    )
    for name in names:
        supplier, was_created = Supplier.objects.get_or_create(restaurant=restaurant, name=name.strip()[:150])
        created += int(was_created)
        StockItem.objects.filter(restaurant=restaurant, supplier_name=name, supplier__isnull=True).update(
            supplier=supplier
        )
    return created


def preferred_supplier_item(stock_item) -> SupplierItem | None:
    rows = list(stock_item.supplier_items.select_related("supplier", "unit").filter(supplier__is_active=True))
    if not rows:
        return None
    preferred = [r for r in rows if r.is_preferred]
    if preferred:
        return preferred[0]
    if stock_item.supplier_id:
        for r in rows:
            if r.supplier_id == stock_item.supplier_id:
                return r
    return rows[0]


def record_price(stock_item, supplier, *, price, unit, source="receive", when=None) -> SupplierItem | None:
    """Keep the supplier's price list current from what we actually paid."""
    if supplier is None or price is None:
        return None
    when = when or timezone.now()
    row, _created = SupplierItem.objects.get_or_create(
        supplier=supplier, stock_item=stock_item, defaults={"unit": unit, "price": price, "last_price_at": when}
    )
    changed = row.price != Decimal(price) or row.unit_id != unit.pk
    if changed or _created:
        row.price = Decimal(price)
        row.unit = unit
        row.last_price_at = when
        row.save(update_fields=["price", "unit", "last_price_at", "updated_at"])
    if changed or _created:
        PriceObservation.objects.create(supplier_item=row, price=Decimal(price), source=source, observed_at=when)
    return row


# ── purchase orders ───────────────────────────────────────────────────────


def next_po_number(restaurant) -> str:
    from apps.payments.services import next_number

    period = timezone.localdate().strftime("%y%m")
    n = next_number(restaurant.pk, "purchase_order", period)
    return f"PO-{period}-{n:03d}"


def create_order(restaurant, *, supplier=None, lines, by=None, expected_on=None, notes="") -> PurchaseOrder:
    """``lines``: [{stock_item, quantity, unit, unit_price?, supplier_item?, note?}]. Empty lines are skipped."""
    with transaction.atomic():
        po = PurchaseOrder.objects.create(
            restaurant=restaurant,
            supplier=supplier,
            number=next_po_number(restaurant),
            expected_on=expected_on
            or (timezone.localdate() + timezone.timedelta(days=(supplier.lead_days if supplier else 1))),
            notes=notes or "",
            created_by=by if getattr(by, "is_authenticated", False) else None,
        )
        for line in lines:
            qty = Decimal(line.get("quantity") or 0)
            if qty <= 0:
                continue
            item = line["stock_item"]
            si = line.get("supplier_item") or (preferred_supplier_item(item) if supplier is None else None)
            if si is None and supplier is not None:
                si = SupplierItem.objects.filter(supplier=supplier, stock_item=item).select_related("unit").first()
            unit = line.get("unit") or (si.unit if si else (item.purchase_unit or item.base_unit))
            price = line.get("unit_price")
            if price is None:
                if si is not None and si.unit_id == unit.pk:
                    price = si.price
                else:
                    per_base = item.current_unit_cost() or ZERO
                    price = (per_base * unit.factor_to_base / item.base_unit.factor_to_base).quantize(Decimal("0.0001"))
            PurchaseOrderLine.objects.create(
                order=po,
                stock_item=item,
                supplier_item=si,
                quantity=qty,
                unit=unit,
                unit_price=Decimal(price or 0),
                note=(line.get("note") or "")[:200],
            )
        po.recompute()
    _audit("po_create", po, by, f"Purchase order {po.number} created ({po.lines.count()} lines)")
    return po


def orders_from_buy_list(restaurant, *, by=None, for_date=None, supplier=None) -> list[PurchaseOrder]:
    """One draft PO per supplier from today's buy list (items without a supplier land on an 'unassigned' PO)."""
    from apps.inventory.services import buy_list

    for_date = for_date or (timezone.localdate() + timezone.timedelta(days=1))
    lines = buy_list(restaurant, for_date=for_date)
    if not lines:
        return []
    groups: dict = {}
    for bl in lines:
        si = preferred_supplier_item(bl.stock_item)
        sup = si.supplier if si is not None else bl.stock_item.supplier
        if supplier is not None and (sup is None or sup.pk != supplier.pk):
            continue
        key = sup.pk if sup is not None else None
        groups.setdefault(key, (sup, []))[1].append(
            {
                "stock_item": bl.stock_item,
                "quantity": bl.suggested_purchase,
                "unit": bl.purchase_unit,
                "supplier_item": si if (si is not None and sup is not None and si.supplier_id == sup.pk) else None,
                "unit_price": (
                    si.price
                    if si is not None and si.unit_id == bl.purchase_unit.pk
                    else (
                        bl.unit_cost * bl.purchase_unit.factor_to_base / bl.stock_item.base_unit.factor_to_base
                    ).quantize(Decimal("0.0001"))
                ),
                "note": ", ".join(bl.reasons),
            }
        )
    out = []
    for _key, (sup, group_lines) in groups.items():
        out.append(create_order(restaurant, supplier=sup, lines=group_lines, by=by, expected_on=for_date))
    return out


def render_text(po: PurchaseOrder) -> str:
    """Plain-text order for email / SMS / WhatsApp / printing."""
    r = po.restaurant
    head = [
        f"{_('Purchase order')} {po.number}",
        f"{r.name}" + (f" · {r.phone}" if getattr(r, "phone", "") else ""),
        f"{_('Supplier')}: {po.supplier.name if po.supplier else '—'}",
        f"{_('Deliver by')}: {po.expected_on:%d.%m.%Y}" if po.expected_on else "",
        "",
    ]
    body = []
    for line in po.lines.select_related("stock_item", "unit"):
        price = f" @ {line.unit_price:.2f}" if line.unit_price else ""
        qty = f"{line.quantity:.4f}".rstrip("0").rstrip(".")
        body.append(f"- {line.stock_item.name}: {qty} {line.unit.code}{price}{' · ' + line.note if line.note else ''}")
    tail = ["", f"{_('Estimated total')}: {po.subtotal:.2f} {getattr(r, 'default_currency', 'GEL')}"]
    if po.notes:
        tail += ["", po.notes]
    return "\n".join(x for x in head + body + tail if x is not None)


def send_order(po: PurchaseOrder, *, by=None, via: str = "auto") -> dict:
    """Mark sent and, when the supplier has an address, deliver it through the notifications module."""
    if po.status not in ("draft", "sent"):
        raise PurchasingError("bad_state", _("Only draft orders can be sent."))
    if not po.lines.exists():
        raise PurchasingError("empty", _("Add at least one line first."))
    from apps.notifications import services as notifications

    text = render_text(po)
    sent = {"email": None, "sms": None}
    sup = po.supplier
    channel_used = "manual"
    if sup is not None:
        if via in ("auto", "email") and sup.email:
            sent["email"] = notifications.send_message(
                po.restaurant,
                "email",
                sup.email,
                text,
                subject=f"{po.restaurant.name}: {po.number}",
                kind="purchase_order",
                ref=po,
                by=by,
                force=True,
            )
            channel_used = "email"
        if via in ("auto", "sms") and sup.phone and (via == "sms" or not sup.email):
            sent["sms"] = notifications.send_message(
                po.restaurant, "sms", sup.phone, text, kind="purchase_order", ref=po, by=by, force=True
            )
            channel_used = "sms" if channel_used == "manual" else channel_used
    po.status = "sent"
    po.sent_at = timezone.now()
    po.sent_via = channel_used
    po.save(update_fields=["status", "sent_at", "sent_via", "updated_at"])
    _audit("po_send", po, by, f"Purchase order {po.number} sent via {channel_used}")
    return {"via": channel_used, "text": text, **sent}


def receive_order(
    po: PurchaseOrder, received: list[dict], *, by=None, reference: str = "", received_at=None
) -> list[StockLot]:
    """
    ``received``: [{line (or line_id), quantity, unit_price?, expiry_date?}] in the line's unit.
    Books one lot per line through the warehouse; partial deliveries keep the PO open.
    """
    from apps.inventory.services import receive_stock

    if po.status not in ("draft", "sent", "partial"):
        raise PurchasingError("bad_state", _("This order is closed."))
    lines = {str(line.pk): line for line in po.lines.select_related("stock_item", "unit", "supplier_item")}
    lots = []
    with transaction.atomic():
        for row in received:
            line = row.get("line") or lines.get(str(row.get("line_id")))
            if line is None:
                continue
            qty = Decimal(row.get("quantity") or 0)
            if qty <= 0:
                continue
            price = row.get("unit_price")
            price = Decimal(price) if price not in (None, "") else Decimal(line.unit_price or 0)
            lot = receive_stock(
                line.stock_item,
                qty,
                line.unit,
                unit_cost=price,
                expiry_date=row.get("expiry_date"),
                supplier_name=po.supplier.name if po.supplier else "",
                reference=reference or po.number,
                received_at=received_at,
                notes=f"PO {po.number}",
                by=by,
            )
            lots.append(lot)
            line.received_qty = Decimal(line.received_qty or 0) + qty
            line.unit_price = price
            line.save(update_fields=["received_qty", "unit_price", "updated_at"])
            if po.supplier is not None:
                record_price(line.stock_item, po.supplier, price=price, unit=line.unit, source="receive")
                StockItem.objects.filter(pk=line.stock_item_id, supplier__isnull=True).update(supplier=po.supplier)
        remaining = any(line.outstanding > ZERO for line in po.lines.all())
        po.status = "partial" if remaining else "received"
        po.received_at = timezone.now()
        if reference:
            po.reference = reference[:100]
        po.recompute(save=False)
        po.save(update_fields=["status", "received_at", "reference", "subtotal", "updated_at"])
    _audit("po_receive", po, by, f"Received {len(lots)} line(s) of {po.number}; status {po.status}")
    return lots


def cancel_order(po: PurchaseOrder, *, by=None) -> PurchaseOrder:
    if po.status == "received":
        raise PurchasingError("bad_state", _("A received order cannot be cancelled."))
    po.status = "cancelled"
    po.save(update_fields=["status", "updated_at"])
    _audit("po_cancel", po, by, f"Purchase order {po.number} cancelled")
    return po


def due_orders(restaurant=None, on=None):
    on = on or timezone.localdate()
    qs = PurchaseOrder.objects.filter(status__in=("sent", "partial"), expected_on=on).select_related(
        "supplier", "restaurant"
    )
    if restaurant is not None:
        qs = qs.filter(restaurant=restaurant)
    return qs


def notify_due(on=None) -> int:
    from apps.notifications import services as notifications

    n = 0
    for po in due_orders(on=on):
        if not enabled(po.restaurant):
            continue
        notifications.notify(
            po.restaurant,
            "purchasing.po_due",
            title=f"{po.number} due today" + (f" · {po.supplier.name}" if po.supplier else ""),
            body=f"{po.lines.count()} lines · {po.subtotal} {getattr(po.restaurant, 'default_currency', 'GEL')}",
            data={"kind": "purchase_order", "id": str(po.pk)},
            dedupe_key=f"purchasing.po_due:{po.pk}:{on or timezone.localdate()}",
        )
        n += 1
    return n


# ── reporting ─────────────────────────────────────────────────────────────


def supplier_report(restaurant, start, end) -> list[dict]:
    """Purchases booked as lots per supplier name in the period (POs and manual receiving alike)."""
    rows = (
        StockLot.objects.filter(restaurant=restaurant, received_at__gte=start, received_at__lt=end)
        .values("supplier_name")
        .annotate(total=Sum("total_cost"), lots=Sum(1))
        .order_by("-total")
    )
    out = []
    for r in rows:
        out.append(
            {"supplier": r["supplier_name"] or _("(no supplier)"), "lots": r["lots"], "total": r["total"] or ZERO}
        )
    return out


def open_summary(restaurant) -> dict:
    qs = PurchaseOrder.objects.filter(restaurant=restaurant)
    today = timezone.localdate()
    month_start = today.replace(day=1)
    return {
        "open": qs.filter(status__in=PurchaseOrder.OPEN).count(),
        "due_today": qs.filter(status__in=("sent", "partial"), expected_on=today).count(),
        "overdue": qs.filter(status__in=("sent", "partial"), expected_on__lt=today).count(),
        "received_this_month": qs.filter(status="received", received_at__date__gte=month_start).aggregate(
            t=Sum("subtotal")
        )["t"]
        or ZERO,
        "suppliers": Supplier.objects.filter(restaurant=restaurant, is_active=True).count(),
    }


def _audit(action, po, user, description):
    try:
        from apps.audit.services import log_action

        log_action(
            action,
            restaurant=po.restaurant,
            user=user if getattr(user, "is_authenticated", False) else None,
            description=description,
            target_model="purchaseorder",
            target_id=str(po.pk),
        )
    except Exception:  # pragma: no cover
        logger.debug("audit skipped", exc_info=True)
