"""
Ticket / receipt / report rendering to raster images.

Cheap ESC/POS printers sold in Georgia have no Georgian code page, so every
document is drawn with Pillow + Noto Sans Georgian and sent as an image.
576 px wide for 80 mm paper, 384 px for 58 mm (203 dpi heads).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Callable

from django.utils import timezone

from PIL import Image, ImageDraw, ImageFont

from apps.printing.escpos import PAPER_WIDTH

FONT_DIR = Path(__file__).resolve().parent / "fonts"


@lru_cache(maxsize=32)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "NotoSansGeorgian-Bold.ttf" if bold else "NotoSansGeorgian-Regular.ttf"
    return ImageFont.truetype(str(FONT_DIR / name), size)


def money(value) -> str:
    return f"{Decimal(value or 0):.2f} ₾"


def when(dt=None) -> str:
    dt = timezone.localtime(dt or timezone.now())
    return dt.strftime("%d.%m.%Y %H:%M")


@dataclass
class Canvas:
    """Collects drawing ops, then paints them onto an image of exactly the used height."""

    width: int = 576
    margin: int = 14
    ops: list[Callable[[ImageDraw.ImageDraw, int], int]] = field(default_factory=list)

    @property
    def inner(self) -> int:
        return self.width - 2 * self.margin

    def _wrap(self, text: str, f: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
        lines: list[str] = []
        for paragraph in (text or "").split("\n"):
            words = paragraph.split(" ")
            current = ""
            for word in words:
                trial = f"{current} {word}".strip()
                if f.getlength(trial) <= max_width or not current:
                    current = trial
                else:
                    lines.append(current)
                    current = word
            lines.append(current)
        return lines or [""]

    def text(
        self, text: str, *, size: int = 26, bold: bool = False, align: str = "left", indent: int = 0, gap: int = 4
    ):
        f = font(size, bold)
        lines = self._wrap(text, f, self.inner - indent)
        line_h = size + gap

        def op(draw: ImageDraw.ImageDraw, y: int) -> int:
            for line in lines:
                w = f.getlength(line)
                if align == "center":
                    x = (self.width - w) / 2
                elif align == "right":
                    x = self.width - self.margin - w
                else:
                    x = self.margin + indent
                draw.text((x, y), line, font=f, fill=0)
                y += line_h
            return line_h * len(lines)

        self.ops.append(op)
        return self

    def row(self, left: str, right: str, *, size: int = 26, bold: bool = False, indent: int = 0, gap: int = 4):
        """Left text (wrapped) with a right-aligned value on the first line."""
        f = font(size, bold)
        right_w = f.getlength(right) + 12
        lines = self._wrap(left, f, self.inner - indent - right_w)
        line_h = size + gap

        def op(draw: ImageDraw.ImageDraw, y: int) -> int:
            draw.text((self.width - self.margin - f.getlength(right), y), right, font=f, fill=0)
            for line in lines:
                draw.text((self.margin + indent, y), line, font=f, fill=0)
                y += line_h
            return line_h * len(lines)

        self.ops.append(op)
        return self

    def rule(self, *, dashed: bool = True, gap: int = 10):
        def op(draw: ImageDraw.ImageDraw, y: int) -> int:
            mid = y + gap // 2
            if dashed:
                x = self.margin
                while x < self.width - self.margin:
                    draw.line((x, mid, min(x + 10, self.width - self.margin), mid), fill=0, width=2)
                    x += 18
            else:
                draw.line((self.margin, mid, self.width - self.margin, mid), fill=0, width=3)
            return gap

        self.ops.append(op)
        return self

    def space(self, px: int = 12):
        self.ops.append(lambda draw, y: px)
        return self

    def render(self) -> Image.Image:
        # Measure on a scratch canvas, then paint on one of the right height.
        scratch = Image.new("L", (self.width, 10), 255)
        measure = ImageDraw.Draw(scratch)
        height = self.margin
        for op in self.ops:
            height += op(measure, -10_000)
        height += self.margin
        image = Image.new("L", (self.width, max(height, 40)), 255)
        draw = ImageDraw.Draw(image)
        y = self.margin
        for op in self.ops:
            y += op(draw, y)
        return image


def _width(printer) -> int:
    return PAPER_WIDTH.get(str(getattr(printer, "paper", "80")), 576)


# ── documents ─────────────────────────────────────────────────────────────

REASON_LABELS = {"new": "", "added": "დამატებული / ADDED", "reprint": "ხელახლა / REPRINT", "manual": ""}


def render_ticket(order, items, *, printer=None, reason: str = "new") -> Image.Image:
    """Kitchen / bar ticket: big order number, where it goes, the items for this printer's stations."""
    c = Canvas(width=_width(printer))
    label = REASON_LABELS.get(reason, reason.upper())
    if label:
        c.text(label, size=28, bold=True, align="center")
    c.text(order.order_number, size=44, bold=True, align="center")
    where = []
    if order.table_id and order.table:
        where.append(f"მაგიდა {order.table.number}")
    type_label = {"dine_in": "ადგილზე", "takeaway": "წასაღები", "delivery": "მიტანა"}.get(
        order.order_type, order.order_type
    )
    where.append(type_label)
    if order.customer_name:
        where.append(order.customer_name)
    c.text(" · ".join(where), size=26, align="center")
    c.text(when(order.confirmed_at or order.created_at), size=22, align="center")
    c.rule()
    for item in items:
        c.row(f"{item.quantity} × {item.item_name}", "", size=34, bold=True)
        mods = [m.modifier_name for m in item.modifiers.all()]
        if mods:
            c.text("+ " + ", ".join(mods), size=26, indent=36)
        if item.special_instructions:
            c.text(f"⚠ {item.special_instructions}", size=28, bold=True, indent=36)
        c.space(8)
    if order.customer_notes:
        c.rule()
        c.text("შენიშვნა / NOTES", size=22, bold=True)
        c.text(order.customer_notes, size=28, bold=True)
    c.rule()
    c.text(f"{printer.name if printer else ''}  {when()}".strip(), size=20, align="center")
    return c.render()


def render_receipt(data: dict, *, printer=None) -> Image.Image:
    """Customer receipt from the dict built by apps.printing.receipts.receipt_data (phase 4 adds fiscal lines)."""
    c = Canvas(width=_width(printer))
    r = data["restaurant"]
    c.text(r.get("name", ""), size=34, bold=True, align="center")
    for line in (r.get("legal_name"), r.get("address"), r.get("phone"), r.get("tax_id_line")):
        if line:
            c.text(line, size=22, align="center")
    if not data.get("fiscal", True):
        c.text("არაფისკალური ჩეკი / NON-FISCAL", size=22, bold=True, align="center")
    c.rule()
    o = data["order"]
    meta = [f"შეკვეთა {o['order_number']}"]
    if o.get("table"):
        meta.append(f"მაგიდა {o['table']}")
    c.text(" · ".join(meta), size=24, bold=True)
    c.text(o.get("created_at", ""), size=22)
    if data.get("number"):
        c.text(f"ჩეკი {data['number']}", size=22)
    c.rule()
    for line in data["lines"]:
        c.row(f"{line['qty']} × {line['name']}", money(line["total"]), size=26)
        if line.get("modifiers"):
            c.text("+ " + ", ".join(line["modifiers"]), size=22, indent=30)
        if line.get("comped"):
            c.text("სახლის ხარჯზე / on the house", size=22, indent=30)
        elif Decimal(line.get("discount") or 0) > 0:
            c.row("ფასდაკლება", f"-{money(line['discount'])}", size=22, indent=30)
    c.rule()
    t = data["totals"]
    c.row("ჯამი", money(t["subtotal"]), size=26)
    if Decimal(t.get("discount") or 0) > 0:
        c.row("ფასდაკლება", f"-{money(t['discount'])}", size=26)
    if Decimal(t.get("service_charge") or 0) > 0:
        c.row("მომსახურება", money(t["service_charge"]), size=26)
    for vat in data.get("vat_breakdown", []):
        c.row(f"დღგ {vat['rate']}%", money(vat["vat"]), size=22)
    if Decimal(t.get("tax") or 0) > 0 and not data.get("vat_breakdown"):
        c.row("გადასახადი", money(t["tax"]), size=26)
    if Decimal(t.get("tip") or 0) > 0:
        c.row("ჩაი", money(t["tip"]), size=26)
    c.row("სულ", money(t["total"]), size=36, bold=True)
    payments = data.get("payments", [])
    if payments:
        c.rule()
        for p in payments:
            c.row(p["method_label"], money(p["amount"]), size=24)
        pay = data.get("payment")
        if pay and Decimal(pay.get("tendered") or 0) > 0:
            c.row("მიღებული", money(pay["tendered"]), size=24)
            c.row("ხურდა", money(pay["change"]), size=24)
        if Decimal(t.get("balance") or 0) > 0:
            c.row("დარჩენილი", money(t["balance"]), size=24, bold=True)
    c.rule()
    if data.get("cashier"):
        c.text(f"მოლარე: {data['cashier']}", size=22)
    c.text(data.get("footer") or "მადლობა! / Thank you!", size=24, align="center")
    c.text(when(), size=20, align="center")
    return c.render()


def render_z_report(shift, report: dict, restaurant, *, printer=None) -> Image.Image:
    c = Canvas(width=_width(printer))
    closed = shift.status == "closed"
    c.text(restaurant.name, size=32, bold=True, align="center")
    c.text(("Z ანგარიში" if closed else "X ანგარიში") + f" · ცვლა #{shift.number}", size=28, bold=True, align="center")
    c.text(f"გახსნა {when(shift.opened_at)}", size=22, align="center")
    if closed and shift.closed_at:
        c.text(f"დახურვა {when(shift.closed_at)}", size=22, align="center")
    c.rule()
    rows = [
        ("გაყიდვები", money(report.get("sales"))),
        ("ჩაი", money(report.get("tips"))),
        ("დაბრუნება", f"-{money(report.get('refunds'))}"),
        ("წმინდა", money(report.get("net_sales"))),
        ("გადახდები", str(report.get("payments_count", 0))),
        ("შეკვეთები", str(report.get("orders_count", 0))),
    ]
    for label, value in rows:
        c.row(label, value, size=26)
    c.rule()
    labels = {
        "cash": "ნაღდი",
        "card_terminal": "ბარათი",
        "online_bog": "ონლაინ BOG",
        "online_flitt": "ონლაინ Flitt",
        "voucher": "ვაუჩერი",
        "other": "სხვა",
        "card": "ბარათი",
        "mobile": "მობილური",
    }
    for method, v in (report.get("by_method") or {}).items():
        c.row(f"{labels.get(method, method)} ({v['count']})", money(v["amount"]), size=24)
    c.rule()
    cash_rows = [
        ("საწყისი ხურდა", money(report.get("opening_float"))),
        ("ნაღდი გაყიდვები", money(report.get("cash_sales"))),
        ("ნაღდი ჩაი", money(report.get("cash_tips"))),
        ("შეტანა", money(report.get("paid_in"))),
        ("გატანა", f"-{money(report.get('paid_out'))}"),
        ("ნაღდი დაბრუნება", f"-{money(report.get('cash_refunds'))}"),
    ]
    for label, value in cash_rows:
        c.row(label, value, size=24)
    c.row("მოსალოდნელი ნაღდი", money(report.get("expected_cash")), size=28, bold=True)
    if closed:
        c.row("დათვლილი", money(report.get("counted_cash")), size=26)
        c.row("სხვაობა", money(report.get("difference")), size=28, bold=True)
    c.rule()
    d = report.get("discounts") or {}
    c.row("ფასდაკლებები", money(Decimal(d.get("orders_amount") or 0) + Decimal(d.get("items_amount") or 0)), size=22)
    c.row("სახლის ხარჯზე", money((report.get("comps") or {}).get("amount")), size=22)
    c.row("წაშლილი", money((report.get("voids") or {}).get("amount")), size=22)
    c.rule()
    c.text(when(), size=20, align="center")
    return c.render()


def render_test(printer, restaurant) -> Image.Image:
    c = Canvas(width=_width(printer))
    c.text("AiMenu", size=40, bold=True, align="center")
    c.text(restaurant.name, size=28, align="center")
    c.text(f"პრინტერი: {printer.name}", size=26, align="center")
    c.text(f"{printer.get_kind_display()} · {printer.paper} mm", size=22, align="center")
    c.rule()
    c.text("ქართული ტექსტი იბეჭდება სწორად.", size=26)
    c.text("Latin text prints too. 0123456789 ₾", size=26)
    c.row("2 × ხინკალი", "12.50 ₾", size=30, bold=True)
    c.rule()
    c.text(when(), size=20, align="center")
    return c.render()
