"""
The receipt as data. One dict feeds the printer image, the POS's browser
print and (phase 4) the fiscal provider; keeping it separate from the
renderer means the fiscal module can add VAT lines without touching pixels.
"""

from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from apps.payments.models import Payment

METHOD_LABELS = {
    "cash": "ნაღდი / Cash",
    "card_terminal": "ბარათი / Card",
    "online_bog": "ონლაინ (BOG)",
    "online_flitt": "ონლაინ (Flitt)",
    "voucher": "ვაუჩერი / Voucher",
    "other": "სხვა / Other",
    "card": "ბარათი / Card",
    "mobile": "მობილური / Mobile",
}


def _user(u) -> str:
    if not u:
        return ""
    return u.get_full_name() or u.email


def receipt_data(order, *, payment: Payment | None = None) -> dict:
    """Everything a receipt shows for ``order``; ``payment`` adds tendered / change / receipt number."""
    from apps.payments import services as ledger

    restaurant = order.restaurant
    lines = []
    for item in order.items.exclude(status="cancelled").prefetch_related("modifiers"):
        lines.append(
            {
                "name": item.item_name,
                "qty": item.quantity,
                "unit_price": str(item.unit_price),
                "total": str(item.net_price),
                "gross": str(item.total_price),
                "discount": str(item.discount_amount or Decimal("0")),
                "comped": bool(item.is_comped),
                "modifiers": [m.modifier_name for m in item.modifiers.all()],
            }
        )
    payments = []
    for a in order.payment_allocations.select_related("payment").order_by("payment__created_at"):
        p = a.payment
        if p.status not in ledger.PAID_STATUSES:
            continue
        payments.append(
            {
                "id": str(p.pk),
                "method": p.payment_method,
                "method_label": METHOD_LABELS.get(p.payment_method, p.payment_method),
                "amount": str(a.amount),
                "tip": str(p.tip_amount),
                "receipt_number": p.receipt_number,
            }
        )
    paid = ledger.paid_amount(order)
    balance = max((order.total or Decimal("0")) - paid, Decimal("0"))
    data = {
        "kind": "receipt",
        "fiscal": False,
        "number": payment.receipt_number if payment else (payments[-1]["receipt_number"] if payments else ""),
        "restaurant": {
            "name": restaurant.name,
            "legal_name": "",
            "address": getattr(restaurant, "address", "") or "",
            "phone": getattr(restaurant, "phone", "") or "",
            "tax_id_line": "",
        },
        "order": {
            "order_number": order.order_number,
            "order_type": order.order_type,
            "table": order.table.number if order.table_id and order.table else "",
            "created_at": timezone.localtime(order.created_at).strftime("%d.%m.%Y %H:%M"),
            "customer_name": order.customer_name or "",
        },
        "lines": lines,
        "totals": {
            "subtotal": str(order.subtotal),
            "discount": str(order.discount_amount),
            "tax": str(order.tax_amount),
            "service_charge": str(order.service_charge),
            "tip": str(order.tip_amount or 0),
            "wallet": str(order.wallet_applied or 0),
            "total": str(order.total),
            "paid": str(paid),
            "balance": str(balance),
        },
        "vat_breakdown": [],
        "payments": payments,
        "payment": None,
        "cashier": "",
        "footer": "",
    }
    if payment is not None:
        data["payment"] = {
            "method": payment.payment_method,
            "method_label": METHOD_LABELS.get(payment.payment_method, payment.payment_method),
            "amount": str(payment.amount),
            "tip": str(payment.tip_amount),
            "tendered": str(payment.tendered or 0),
            "change": str(payment.change_given or 0),
        }
        data["cashier"] = _user(payment.processed_by)
    return data
