"""
VAT arithmetic. Pure Decimal maths, quantized 0.01 half-up at every stored
figure, so a receipt's lines always add up to its totals.

Georgian menus show gross prices (VAT included); small businesses under the
registration threshold are not VAT payers. Both cases are one ``TaxContext``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0")
HUNDRED = Decimal("100")


def q(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class TaxContext:
    rate: Decimal  # e.g. 18.00; 0 for non-payers
    inclusive: bool  # prices already contain VAT
    vat_payer: bool
    profile: object | None = None

    @property
    def label(self) -> str:
        if not self.vat_payer or self.rate == ZERO:
            return "არ არის დღგ-ს გადამხდელი / Not a VAT payer"
        return f"დღგ {self.rate.normalize()}% {'ჩათვლით' if self.inclusive else 'დამატებით'}"


def tax_context(restaurant) -> TaxContext:
    """The restaurant's fiscal profile, or the legacy exclusive ``tax_rate``."""
    from apps.fiscal.models import FiscalProfile

    profile = FiscalProfile.objects.filter(restaurant_id=restaurant.pk).first() if restaurant else None
    if profile is not None:
        return TaxContext(
            rate=profile.effective_vat_rate,
            inclusive=profile.prices_include_vat,
            vat_payer=profile.vat_payer,
            profile=profile,
        )
    rate = Decimal(getattr(restaurant, "tax_rate", 0) or 0)
    return TaxContext(rate=rate, inclusive=False, vat_payer=rate > ZERO, profile=None)


def split_gross(gross, rate) -> tuple[Decimal, Decimal]:
    """Gross that already contains VAT -> (net, vat)."""
    gross = Decimal(gross or 0)
    rate = Decimal(rate or 0)
    if rate == ZERO:
        return q(gross), ZERO
    vat = q(gross - gross / (1 + rate / HUNDRED))
    return q(gross - vat), vat


def add_vat(net, rate) -> tuple[Decimal, Decimal]:
    """Net price -> (gross, vat)."""
    net = Decimal(net or 0)
    rate = Decimal(rate or 0)
    vat = q(net * rate / HUNDRED)
    return q(net + vat), vat


def allocate(total, weights) -> list[Decimal]:
    """Split ``total`` across ``weights`` proportionally, exact to the cent (largest remainder)."""
    total = q(total)
    weights = [Decimal(w or 0) for w in weights]
    n = len(weights)
    if n == 0:
        return []
    wsum = sum(weights, ZERO)
    if wsum == ZERO or total == ZERO:
        return [ZERO] * n
    raw = [total * w / wsum for w in weights]
    floored = [Decimal(int(r * 100)) / 100 for r in raw]
    remainder = int(round((total - sum(floored, ZERO)) * 100))
    order = sorted(range(n), key=lambda i: (raw[i] - floored[i]), reverse=True)
    out = list(floored)
    for i in order[:remainder]:
        out[i] += CENT
    return [q(x) for x in out]


@dataclass
class Breakdown:
    rate: Decimal
    inclusive: bool
    vat_payer: bool
    lines: list[dict]
    breakdown: list[dict]
    net_total: Decimal
    vat_total: Decimal
    gross_total: Decimal
    label: str


def vat_breakdown(order, *, amount=None, ctx: TaxContext | None = None) -> Breakdown:
    """
    Per-line VAT for a receipt. Order-level discounts and wallet credit are
    spread over the lines by value; the service charge is a line of its own
    (it is the restaurant's revenue, so it carries VAT); tips are outside VAT.
    ``amount`` (a partial payment) scales every line proportionally.
    """
    ctx = ctx or tax_context(order.restaurant)
    items = list(order.items.exclude(status="cancelled").prefetch_related("modifiers").order_by("created_at"))
    line_values = [q(i.net_price) for i in items]
    names = [f"{i.quantity} × {i.item_name}" for i in items]
    qtys = [i.quantity for i in items]
    if order.service_charge and order.service_charge > ZERO:
        line_values.append(q(order.service_charge))
        names.append("მომსახურება / Service")
        qtys.append(1)

    # Order-level discounts + wallet credit reduce the item lines proportionally
    # (the service charge was computed on the discounted net already).
    n_items = len(items)
    item_level = sum((q(i.discount_amount) for i in items), ZERO)
    order_level = max(q(order.discount_amount) - item_level, ZERO) + q(order.wallet_applied or 0)
    if order_level > ZERO and n_items:
        item_values = line_values[:n_items]
        cuts = allocate(min(order_level, sum(item_values, ZERO)), item_values)
        line_values = [max(v - c, ZERO) for v, c in zip(item_values, cuts)] + line_values[n_items:]

    # Partial payment: the receipt covers a share of every line.
    charged = sum(line_values, ZERO)
    if ctx.inclusive:
        charged_gross = charged
    else:
        charged_gross = sum((add_vat(v, ctx.rate)[0] for v in line_values), ZERO)
    if amount is not None and charged_gross > ZERO and q(amount) < charged_gross:
        share = allocate(q(amount), line_values)
        if ctx.inclusive:
            line_values = share
        else:
            # ``amount`` is gross; bring it back to net shares.
            line_values = [split_gross(s, ctx.rate)[0] for s in share]

    lines = []
    for name, qty, value in zip(names, qtys, line_values):
        if ctx.inclusive:
            gross = value
            net, vat = split_gross(gross, ctx.rate)
        else:
            net = value
            gross, vat = add_vat(net, ctx.rate)
        lines.append(
            {"name": name, "qty": qty, "net": str(net), "vat": str(vat), "gross": str(gross), "vat_rate": str(ctx.rate)}
        )
    net_total = sum((Decimal(l["net"]) for l in lines), ZERO)
    vat_total = sum((Decimal(l["vat"]) for l in lines), ZERO)
    gross_total = sum((Decimal(l["gross"]) for l in lines), ZERO)
    breakdown = (
        [{"rate": str(ctx.rate), "net": str(net_total), "vat": str(vat_total), "gross": str(gross_total)}]
        if ctx.vat_payer and ctx.rate > ZERO
        else []
    )
    return Breakdown(
        rate=ctx.rate,
        inclusive=ctx.inclusive,
        vat_payer=ctx.vat_payer,
        lines=lines,
        breakdown=breakdown,
        net_total=q(net_total),
        vat_total=q(vat_total),
        gross_total=q(gross_total),
        label=ctx.label,
    )
