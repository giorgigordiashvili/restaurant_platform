"""
Pure report queries: ``fn(restaurant, period) -> dict / list``.

Conventions
* Sales = orders the kitchen accepted (confirmed ... completed), which is
  also when stock is consumed; ``cancelled`` orders are cancellations.
* net_sales = Σ(subtotal − discounts); gross_total = Σ total (what the
  guest paid incl. tax, service, tip, minus wallet).
* Decimals are never rounded in SQL; :func:`money` quantizes at the edge.
* Day / hour buckets are in the restaurant's timezone (``period.tz``).
"""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Case, Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, ExtractHour, TruncDate

from apps.orders.models import Order, OrderDiscount, OrderItem
from apps.payments.models import CashShift, Payment, Refund

SALES_STATUSES = ("confirmed", "preparing", "ready", "served", "completed")
ZERO = Decimal("0")
CENT = Decimal("0.01")
MONEY = DecimalField(max_digits=12, decimal_places=2)


def money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def pct(part, whole) -> Decimal:
    part = Decimal(part or 0)
    whole = Decimal(whole or 0)
    if whole == 0:
        return ZERO
    return (part / whole * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def delta_pct(current, previous) -> Decimal | None:
    previous = Decimal(previous or 0)
    if previous == 0:
        return None
    return ((Decimal(current or 0) - previous) / previous * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


NET_EXPR = ExpressionWrapper(F("subtotal") - F("discount_amount"), output_field=MONEY)
ITEM_NET_EXPR = ExpressionWrapper(F("total_price") - F("discount_amount"), output_field=MONEY)


def _sum(expr):
    return Coalesce(Sum(expr, output_field=MONEY), Value(ZERO), output_field=MONEY)


def _in_period(qs, period, field="created_at"):
    return qs.filter(**{f"{field}__gte": period.start, f"{field}__lt": period.end})


def orders_in(restaurant, period):
    return _in_period(Order.objects.filter(restaurant=restaurant), period)


def sales_orders(restaurant, period):
    return orders_in(restaurant, period).filter(status__in=SALES_STATUSES)


def _user_label(row, prefix) -> str:
    first = row.get(f"{prefix}__first_name") or ""
    last = row.get(f"{prefix}__last_name") or ""
    name = f"{first} {last}".strip()
    return name or row.get(f"{prefix}__email") or "—"


# ── sales ─────────────────────────────────────────────────────────────────


def sales_summary(restaurant, period) -> dict:
    qs = sales_orders(restaurant, period)
    agg = qs.aggregate(
        orders=Count("id"),
        subtotal=_sum("subtotal"),
        discounts=_sum("discount_amount"),
        gross=_sum("total"),
        service=_sum("service_charge"),
        tax=_sum("tax_amount"),
        tips=_sum("tip_amount"),
        wallet=_sum("wallet_applied"),
    )
    net = agg["subtotal"] - agg["discounts"]
    cancelled = orders_in(restaurant, period).filter(status="cancelled").aggregate(n=Count("id"), value=_sum("total"))
    voids = _in_period(
        OrderItem.objects.filter(order__restaurant=restaurant, status="cancelled"), period, "voided_at"
    ).aggregate(n=Count("id"), value=_sum("total_price"))
    refunds = _in_period(
        Refund.objects.filter(restaurant=restaurant, status="completed"), period, "completed_at"
    ).aggregate(n=Count("id"), value=_sum("amount"))
    orders = agg["orders"] or 0
    return {
        "orders": orders,
        "net_sales": money(net),
        "gross_total": money(agg["gross"]),
        "avg_ticket": money(agg["gross"] / orders) if orders else ZERO,
        "discounts": money(agg["discounts"]),
        "service_charge": money(agg["service"]),
        "tax": money(agg["tax"]),
        "tips": money(agg["tips"]),
        "wallet": money(agg["wallet"]),
        "cancellations": {"count": cancelled["n"] or 0, "value": money(cancelled["value"])},
        "voids": {"count": voids["n"] or 0, "value": money(voids["value"])},
        "refunds": {"count": refunds["n"] or 0, "value": money(refunds["value"])},
    }


def compare_period(restaurant, period) -> dict:
    current = sales_summary(restaurant, period)
    previous = sales_summary(restaurant, period.previous())
    return {
        "current": current,
        "previous": previous,
        "delta": {
            key: delta_pct(current[key], previous[key]) for key in ("orders", "net_sales", "gross_total", "avg_ticket")
        },
    }


def sales_by_day(restaurant, period) -> list[dict]:
    rows = (
        sales_orders(restaurant, period)
        .annotate(day=TruncDate("created_at", tzinfo=period.tz))
        .values("day")
        .annotate(orders=Count("id"), net=_sum(NET_EXPR), gross=_sum("total"))
    )
    by_day = {r["day"]: r for r in rows}
    out = []
    for d in period.dates():
        r = by_day.get(d, {})
        out.append({"day": d, "orders": r.get("orders", 0), "net": money(r.get("net")), "gross": money(r.get("gross"))})
    return out


def sales_by_hour(restaurant, period) -> list[dict]:
    rows = (
        sales_orders(restaurant, period)
        .annotate(hour=ExtractHour("created_at", tzinfo=period.tz))
        .values("hour")
        .annotate(orders=Count("id"), net=_sum(NET_EXPR))
    )
    by_hour = {r["hour"]: r for r in rows}
    return [
        {"hour": h, "orders": by_hour.get(h, {}).get("orders", 0), "net": money(by_hour.get(h, {}).get("net"))}
        for h in range(24)
    ]


def sales_by_type(restaurant, period) -> list[dict]:
    rows = (
        sales_orders(restaurant, period)
        .values("order_type")
        .annotate(orders=Count("id"), net=_sum(NET_EXPR), gross=_sum("total"))
        .order_by("-gross")
    )
    labels = dict(Order.ORDER_TYPE_CHOICES)
    return [
        {
            "order_type": r["order_type"],
            "label": str(labels.get(r["order_type"], r["order_type"])),
            "orders": r["orders"],
            "net": money(r["net"]),
            "gross": money(r["gross"]),
        }
        for r in rows
    ]


def sales_by_method(restaurant, period) -> list[dict]:
    """Payments taken in the period by method, plus an 'unrecorded' row so the table reconciles to sales."""
    rows = (
        _in_period(
            Payment.objects.filter(restaurant=restaurant, status__in=("completed", "partially_refunded", "refunded")),
            period,
            "completed_at",
        )
        .values("payment_method")
        .annotate(count=Count("id"), amount=_sum("amount"), tips=_sum("tip_amount"))
        .order_by("-amount")
    )
    labels = dict(Payment.PAYMENT_METHOD_CHOICES)
    out = [
        {
            "method": r["payment_method"],
            "label": str(labels.get(r["payment_method"], r["payment_method"])),
            "count": r["count"],
            "amount": money(r["amount"]),
            "tips": money(r["tips"]),
        }
        for r in rows
    ]
    gross = sales_summary(restaurant, period)["gross_total"]
    recorded = sum((r["amount"] for r in out), ZERO)
    if gross - recorded > CENT:
        out.append(
            {
                "method": "unrecorded",
                "label": "Unrecorded / unpaid",
                "count": 0,
                "amount": money(gross - recorded),
                "tips": ZERO,
            }
        )
    return out


# ── menu ──────────────────────────────────────────────────────────────────


def _category_names(menu_item_ids) -> dict:
    from apps.menu.models import MenuItem

    names = {}
    qs = MenuItem.objects.filter(pk__in=[i for i in menu_item_ids if i]).select_related("category")
    for item in qs.prefetch_related("category__translations"):
        cat = item.category
        names[item.pk] = (
            cat.safe_translation_getter("name", any_language=True) if cat else "—",
            cat.pk if cat else None,
        )
    return names


def items_report(restaurant, period) -> list[dict]:
    """Per dish: quantity, net revenue, share, modifiers per line. Deleted dishes survive via the snapshot name."""
    rows = (
        OrderItem.objects.filter(order__in=sales_orders(restaurant, period))
        .exclude(status="cancelled")
        .values("menu_item_id", "item_name")
        .annotate(
            qty=Coalesce(Sum("quantity"), Value(0)),
            revenue=_sum(ITEM_NET_EXPR),
            lines=Count("id", distinct=True),
            modifiers=Count("modifiers"),
            comps=Count("id", filter=Q(is_comped=True), distinct=True),
        )
    )
    merged: dict = {}
    for r in rows:
        key = r["menu_item_id"] or f"name:{r['item_name']}"
        m = merged.setdefault(
            key,
            {
                "menu_item_id": r["menu_item_id"],
                "name": r["item_name"],
                "qty": 0,
                "revenue": ZERO,
                "lines": 0,
                "modifiers": 0,
                "comps": 0,
            },
        )
        m["qty"] += r["qty"]
        m["revenue"] += r["revenue"]
        m["lines"] += r["lines"]
        m["modifiers"] += r["modifiers"]
        m["comps"] += r["comps"]
    names = _category_names(m["menu_item_id"] for m in merged.values())
    total_qty = sum(m["qty"] for m in merged.values()) or 0
    total_rev = sum((m["revenue"] for m in merged.values()), ZERO)
    out = []
    for m in merged.values():
        cat_name, cat_id = names.get(m["menu_item_id"], ("—", None))
        out.append(
            {
                **m,
                "revenue": money(m["revenue"]),
                "category": cat_name,
                "category_id": cat_id,
                "share_qty": pct(m["qty"], total_qty),
                "share_revenue": pct(m["revenue"], total_rev),
                "avg_price": money(m["revenue"] / m["qty"]) if m["qty"] else ZERO,
                "modifiers_per_line": (
                    (Decimal(m["modifiers"]) / m["lines"]).quantize(Decimal("0.01")) if m["lines"] else ZERO
                ),
            }
        )
    out.sort(key=lambda r: (-r["revenue"], -r["qty"]))
    return out


def categories_report(restaurant, period) -> list[dict]:
    items = items_report(restaurant, period)
    groups: dict = defaultdict(lambda: {"qty": 0, "revenue": ZERO, "dishes": 0})
    for r in items:
        g = groups[r["category"]]
        g["qty"] += r["qty"]
        g["revenue"] += r["revenue"]
        g["dishes"] += 1
    total = sum((g["revenue"] for g in groups.values()), ZERO)
    out = [
        {
            "category": name,
            "dishes": g["dishes"],
            "qty": g["qty"],
            "revenue": money(g["revenue"]),
            "share": pct(g["revenue"], total),
        }
        for name, g in groups.items()
    ]
    out.sort(key=lambda r: -r["revenue"])
    return out


def _cogs_by_menu_item(restaurant, period) -> dict:
    from apps.inventory.models import StockMovement

    rows = (
        _in_period(
            StockMovement.objects.filter(restaurant=restaurant, kind__in=("consume_sale", "restore_sale")), period
        )
        .exclude(order_item__menu_item_id__isnull=True)
        .values("order_item__menu_item_id")
        .annotate(
            cost=_sum(
                Case(
                    When(kind="consume_sale", then=F("total_cost")),
                    default=ExpressionWrapper(-F("total_cost"), output_field=MONEY),
                    output_field=MONEY,
                )
            )
        )
    )
    return {r["order_item__menu_item_id"]: r["cost"] for r in rows}


def menu_engineering(restaurant, period) -> dict:
    """
    Kasavana–Smith quadrants on the tracked dishes (those with a recipe, so
    a COGS exists): popularity vs contribution margin per portion.
    star = popular & profitable, plowhorse = popular & thin, puzzle =
    profitable & rare, dog = neither.
    """
    items = items_report(restaurant, period)
    cogs = _cogs_by_menu_item(restaurant, period)
    tracked = [r for r in items if r["menu_item_id"] in cogs and r["qty"]]
    untracked = [r for r in items if r["menu_item_id"] not in cogs]
    total_qty = sum(r["qty"] for r in tracked)
    rows = []
    for r in tracked:
        cost = money(cogs[r["menu_item_id"]])
        margin_total = r["revenue"] - cost
        rows.append(
            {
                **r,
                "cogs": cost,
                "margin": money(margin_total),
                "margin_per_unit": money(margin_total / r["qty"]),
                "food_cost_pct": pct(cost, r["revenue"]),
            }
        )
    if rows:
        # 70 % of the average share is the classic popularity threshold.
        pop_threshold = Decimal("0.7") * Decimal(100) / len(rows)
        weighted_margin = sum((x["margin"] for x in rows), ZERO) / (total_qty or 1)
        for x in rows:
            popular = x["share_qty"] >= pop_threshold
            profitable = x["margin_per_unit"] >= weighted_margin
            x["quadrant"] = (
                "star" if popular and profitable else "plowhorse" if popular else "puzzle" if profitable else "dog"
            )
    else:
        pop_threshold = ZERO
        weighted_margin = ZERO
    quadrants = {k: [x for x in rows if x.get("quadrant") == k] for k in ("star", "plowhorse", "puzzle", "dog")}
    return {
        "rows": rows,
        "quadrants": quadrants,
        "untracked": untracked,
        "popularity_threshold": pop_threshold,
        "margin_threshold": money(weighted_margin),
    }


def food_cost(restaurant, period) -> dict:
    from apps.inventory.models import StockMovement, WasteEntry

    movements = _in_period(StockMovement.objects.filter(restaurant=restaurant), period)
    cogs = movements.aggregate(
        consumed=_sum(Case(When(kind="consume_sale", then=F("total_cost")), default=Value(ZERO), output_field=MONEY)),
        restored=_sum(Case(When(kind="restore_sale", then=F("total_cost")), default=Value(ZERO), output_field=MONEY)),
        meals=_sum(Case(When(kind="employee_meal", then=F("total_cost")), default=Value(ZERO), output_field=MONEY)),
        received=_sum(Case(When(kind="receive", then=F("total_cost")), default=Value(ZERO), output_field=MONEY)),
    )
    waste = WasteEntry.objects.filter(
        restaurant=restaurant, occurred_on__gte=period.start_date, occurred_on__lte=period.end_date
    ).aggregate(value=_sum("total_cost"), n=Count("id"))
    net_sales = sales_summary(restaurant, period)["net_sales"]
    cost_of_sales = money(cogs["consumed"] - cogs["restored"])
    return {
        "net_sales": net_sales,
        "cogs": cost_of_sales,
        "waste": money(waste["value"]),
        "waste_count": waste["n"] or 0,
        "employee_meals": money(cogs["meals"]),
        "purchases": money(cogs["received"]),
        "gross_margin": money(net_sales - cost_of_sales),
        "gross_margin_pct": pct(net_sales - cost_of_sales, net_sales),
        "food_cost_pct": pct(cost_of_sales, net_sales),
        "waste_pct": pct(waste["value"], net_sales),
    }


# ── staff ─────────────────────────────────────────────────────────────────


def staff_report(restaurant, period) -> dict:
    servers = (
        sales_orders(restaurant, period)
        .exclude(server__isnull=True)
        .values("server_id", "server__first_name", "server__last_name", "server__email")
        .annotate(orders=Count("id"), sales=_sum("total"), tips=_sum("tip_amount"))
        .order_by("-sales")
    )
    handlers = (
        orders_in(restaurant, period)
        .exclude(handled_by__isnull=True)
        .values("handled_by_id", "handled_by__first_name", "handled_by__last_name", "handled_by__email")
        .annotate(
            orders=Count("id"),
            cancelled=Count("id", filter=Q(status="cancelled")),
            sales=_sum(Case(When(status__in=SALES_STATUSES, then=F("total")), default=Value(ZERO), output_field=MONEY)),
        )
        .order_by("-orders")
    )
    discounts = (
        _in_period(OrderDiscount.objects.filter(order__restaurant=restaurant), period)
        .exclude(applied_by__isnull=True)
        .values("applied_by_id")
        .annotate(n=Count("id"), amount=_sum("amount"))
    )
    voids = (
        _in_period(OrderItem.objects.filter(order__restaurant=restaurant, status="cancelled"), period, "voided_at")
        .exclude(voided_by__isnull=True)
        .values("voided_by_id")
        .annotate(n=Count("id"), amount=_sum("total_price"))
    )
    d_by = {r["applied_by_id"]: r for r in discounts}
    v_by = {r["voided_by_id"]: r for r in voids}
    return {
        "servers": [
            {
                "user_id": str(r["server_id"]),
                "name": _user_label(r, "server"),
                "orders": r["orders"],
                "sales": money(r["sales"]),
                "tips": money(r["tips"]),
                "avg_ticket": money(r["sales"] / r["orders"]) if r["orders"] else ZERO,
            }
            for r in servers
        ],
        "handlers": [
            {
                "user_id": str(r["handled_by_id"]),
                "name": _user_label(r, "handled_by"),
                "orders": r["orders"],
                "sales": money(r["sales"]),
                "cancelled": r["cancelled"],
                "discounts": d_by.get(r["handled_by_id"], {}).get("n", 0),
                "discounts_amount": money(d_by.get(r["handled_by_id"], {}).get("amount")),
                "voids": v_by.get(r["handled_by_id"], {}).get("n", 0),
                "voids_amount": money(v_by.get(r["handled_by_id"], {}).get("amount")),
            }
            for r in handlers
        ],
    }


# ── shifts ────────────────────────────────────────────────────────────────


def shifts_report(restaurant, period) -> list[dict]:
    rows = []
    for s in _in_period(CashShift.objects.filter(restaurant=restaurant), period, "opened_at").select_related(
        "opened_by", "closed_by"
    ):
        r = s.report or {}
        rows.append(
            {
                "id": str(s.pk),
                "number": s.number,
                "status": s.status,
                "opened_at": s.opened_at,
                "closed_at": s.closed_at,
                "opened_by": (s.opened_by.get_full_name() or s.opened_by.email) if s.opened_by else "—",
                "sales": money(r.get("sales")),
                "tips": money(r.get("tips")),
                "refunds": money(r.get("refunds")),
                "payments": r.get("payments_count", 0),
                "expected_cash": s.expected_cash,
                "counted_cash": s.counted_cash,
                "difference": s.difference,
            }
        )
    return rows


# ── reservations ──────────────────────────────────────────────────────────


def reservations_report(restaurant, period) -> dict:
    from apps.reservations.models import Reservation

    qs = Reservation.objects.filter(
        restaurant=restaurant, reservation_date__gte=period.start_date, reservation_date__lte=period.end_date
    )
    by_status = {r["status"]: r["n"] for r in qs.values("status").annotate(n=Count("id"))}
    total = sum(by_status.values())
    honoured = ("confirmed", "seated", "completed")
    covers = qs.filter(status__in=honoured).aggregate(n=Coalesce(Sum("party_size"), Value(0)))["n"]
    no_show = by_status.get("no_show", 0)
    cancelled = by_status.get("cancelled", 0)
    daily = {
        r["reservation_date"]: r
        for r in qs.values("reservation_date").annotate(
            n=Count("id"), covers=Coalesce(Sum("party_size", filter=Q(status__in=honoured)), Value(0))
        )
    }
    by_source = [
        {"source": r["source"], "n": r["n"]} for r in qs.values("source").annotate(n=Count("id")).order_by("-n")
    ]
    return {
        "total": total,
        "by_status": by_status,
        "covers": covers,
        "no_show": no_show,
        "cancelled": cancelled,
        "no_show_rate": pct(no_show, total - cancelled),
        "by_day": [
            {"day": d, "reservations": daily.get(d, {}).get("n", 0), "covers": daily.get(d, {}).get("covers", 0)}
            for d in period.dates()
        ],
        "by_source": by_source,
    }


# ── reviews ───────────────────────────────────────────────────────────────


def reviews_report(restaurant, period) -> dict:
    from django.db.models import Avg

    from apps.reviews.models import Review

    qs = _in_period(Review.objects.filter(restaurant=restaurant, is_hidden=False), period)
    agg = qs.aggregate(n=Count("id"), avg=Avg("rating"))
    distribution = {i: 0 for i in range(1, 6)}
    for r in qs.values("rating").annotate(n=Count("id")):
        distribution[r["rating"]] = r["n"]
    daily = {
        r["day"]: r
        for r in qs.annotate(day=TruncDate("created_at", tzinfo=period.tz))
        .values("day")
        .annotate(n=Count("id"), avg=Avg("rating"))
    }
    return {
        "count": agg["n"] or 0,
        "average": Decimal(agg["avg"] or 0).quantize(Decimal("0.01")),
        "distribution": distribution,
        "by_day": [
            {
                "day": d,
                "reviews": daily.get(d, {}).get("n", 0),
                "average": Decimal(daily.get(d, {}).get("avg") or 0).quantize(Decimal("0.01")),
            }
            for d in period.dates()
        ],
    }
