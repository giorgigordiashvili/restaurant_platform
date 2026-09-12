"""
Fiscal documents: create (numbered, queued), issue through the provider,
render for printing. Idempotent everywhere -- payment webhooks are retried.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.enqueue import enqueue
from apps.fiscal import vat
from apps.fiscal.models import FiscalDocument, FiscalProfile
from apps.fiscal.providers import get_provider
from apps.payments.services import next_number

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
ISSUABLE = ("receipt", "refund")
TERMINAL = ("confirmed", "cancelled")


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "fiscal_enabled", False))


def profile_for(restaurant) -> FiscalProfile:
    profile, created = FiscalProfile.objects.get_or_create(
        restaurant=restaurant, defaults=_defaults_from_tax_rate(restaurant)
    )
    return profile


def _defaults_from_tax_rate(restaurant) -> dict:
    """Existing tenants keep their totals: an exclusive tax_rate becomes an exclusive VAT payer profile."""
    rate = Decimal(getattr(restaurant, "tax_rate", 0) or 0)
    if rate > ZERO:
        return {"vat_payer": True, "vat_rate": rate, "prices_include_vat": False}
    return {"vat_payer": False, "vat_rate": Decimal("18.00"), "prices_include_vat": True}


def backoff(attempts: int) -> int:
    return min(30 * (2 ** max(attempts - 1, 0)), 900)


def _number(restaurant, profile, kind: str) -> str:
    prefixes = {"receipt": profile.receipt_prefix or "R", "refund": "RF", "waybill": "WB", "invoice": "INV"}
    n = next_number(restaurant.pk, kind)
    return f"{prefixes.get(kind, kind.upper())}-{n:06d}"


# ── receipts / refunds ────────────────────────────────────────────────────


def create_receipt(payment) -> FiscalDocument | None:
    """One receipt per completed payment. Runs in the caller's transaction; issuing happens after commit."""
    order = payment.order
    if order is None:
        return None
    restaurant = payment.restaurant or order.restaurant
    if not enabled(restaurant):
        return None
    existing = FiscalDocument.objects.filter(payment=payment, kind="receipt").exclude(status="cancelled").first()
    if existing is not None:
        return existing
    profile = profile_for(restaurant)
    bd = vat.vat_breakdown(order, amount=payment.amount)
    with transaction.atomic():
        number = _number(restaurant, profile, "receipt")
        doc = FiscalDocument.objects.create(
            restaurant=restaurant,
            kind="receipt",
            status="queued",
            provider=profile.provider,
            payment=payment,
            order=order,
            fiscal_number=number,
            vat_rate=bd.rate,
            prices_include_vat=bd.inclusive,
            lines=bd.lines,
            vat_breakdown=bd.breakdown,
            net_total=bd.net_total,
            vat_total=bd.vat_total,
            gross_total=bd.gross_total,
            payload={
                "payment_method": payment.payment_method,
                "cashier_id": str(payment.processed_by_id or ""),
                "tip": str(payment.tip_amount or 0),
                "vat_label": bd.label,
            },
        )
        type(payment).objects.filter(pk=payment.pk).update(receipt_number=number)
        payment.receipt_number = number
    from apps.fiscal import tasks

    enqueue(tasks.issue_document, str(doc.pk))
    return doc


def create_refund_document(
    *, restaurant, amount, refund=None, order=None, reverses=None, by=None
) -> FiscalDocument | None:
    if not enabled(restaurant):
        return None
    if (
        refund is not None
        and FiscalDocument.objects.filter(refund=refund, kind="refund").exclude(status="cancelled").exists()
    ):
        return FiscalDocument.objects.filter(refund=refund, kind="refund").exclude(status="cancelled").first()
    profile = profile_for(restaurant)
    order = (
        order or (refund.order if refund is not None else None) or (reverses.order if reverses is not None else None)
    )
    if order is not None:
        bd = vat.vat_breakdown(order, amount=amount)
        lines, breakdown, net, vat_total, gross = bd.lines, bd.breakdown, bd.net_total, bd.vat_total, bd.gross_total
        rate, inclusive = bd.rate, bd.inclusive
    else:
        ctx = vat.tax_context(restaurant)
        gross = vat.q(amount)
        net, vat_total = vat.split_gross(gross, ctx.rate) if ctx.inclusive else (gross, ZERO)
        lines, breakdown, rate, inclusive = [], [], ctx.rate, ctx.inclusive
    with transaction.atomic():
        doc = FiscalDocument.objects.create(
            restaurant=restaurant,
            kind="refund",
            status="queued",
            provider=profile.provider,
            refund=refund,
            order=order,
            payment=reverses.payment if reverses is not None else (refund.payment if refund is not None else None),
            reverses=reverses,
            fiscal_number=_number(restaurant, profile, "refund"),
            vat_rate=rate,
            prices_include_vat=inclusive,
            lines=lines,
            vat_breakdown=breakdown,
            net_total=net,
            vat_total=vat_total,
            gross_total=gross,
            payload={
                "amount": str(vat.q(amount)),
                "reason": getattr(refund, "reason", "") if refund else "order_cancelled",
            },
        )
    from apps.fiscal import tasks

    enqueue(tasks.issue_document, str(doc.pk))
    return doc


# ── waybills ──────────────────────────────────────────────────────────────


def create_inbound_waybill(lot) -> FiscalDocument | None:
    """A received delivery with an invoice reference becomes a draft inbound waybill (manual match / export)."""
    restaurant = lot.restaurant
    if not enabled(restaurant) or not lot.reference:
        return None
    if FiscalDocument.objects.filter(stock_lot=lot, kind="waybill_in").exists():
        return None
    profile = profile_for(restaurant)
    item = lot.stock_item
    unit = getattr(getattr(item, "base_unit", None), "code", "") or ""
    qty = vat.q(lot.received_qty)
    goods = [
        {
            "id": 0,
            "name": item.name,
            "unit": unit,
            "quantity": str(qty),
            "price": str(vat.q(lot.unit_cost)),
            "amount": str(vat.q(lot.total_cost)),
            "vat_type": 0,
        }
    ]
    return FiscalDocument.objects.create(
        restaurant=restaurant,
        kind="waybill_in",
        status="draft",
        provider=profile.provider,
        stock_lot=lot,
        fiscal_number=_number(restaurant, profile, "waybill"),
        gross_total=vat.q(lot.total_cost),
        payload={
            "type": 3,
            "reference": lot.reference,
            "supplier": lot.supplier_name,
            "buyer_tin": profile.tax_id,
            "buyer_name": profile.legal_name or restaurant.name,
            "end_address": profile.legal_address,
            "goods": goods,
            "full_amount": str(vat.q(lot.total_cost)),
            "comment": f"Delivery {lot.reference} from {lot.supplier_name}".strip(),
        },
    )


# ── issuing ───────────────────────────────────────────────────────────────


def issue(doc: FiscalDocument, *, session=None) -> FiscalDocument:
    """Hand the document to the provider. Row-locked bookkeeping; the network call runs outside the lock."""
    with transaction.atomic():
        doc = FiscalDocument.objects.select_for_update().select_related("restaurant").get(pk=doc.pk)
        if doc.status in TERMINAL:
            return doc
        profile = profile_for(doc.restaurant)
        doc.attempts += 1
        doc.status = "sent"
        doc.provider = profile.provider
        doc.save(update_fields=["attempts", "status", "provider", "updated_at"])
    provider = get_provider(profile, session=session)
    method = {"receipt": provider.issue_receipt, "refund": provider.issue_refund}.get(doc.kind, provider.send_waybill)
    try:
        result = method(doc)
    except Exception as exc:  # noqa: BLE001 - a provider bug must not lose the document
        logger.exception("Fiscal provider %s failed for %s", provider.code, doc.pk)
        from apps.fiscal.providers.base import ProviderResult

        result = ProviderResult(ok=False, error=str(exc)[:500], retryable=True)
    with transaction.atomic():
        doc = FiscalDocument.objects.select_for_update().get(pk=doc.pk)
        doc.response = result.response or {}
        if result.ok:
            doc.status = "confirmed"
            doc.is_fiscal = provider.is_fiscal
            doc.external_id = result.external_id or doc.external_id
            if result.fiscal_number:
                doc.fiscal_number = result.fiscal_number
            doc.issued_at = timezone.now()
            doc.error = ""
            doc.next_retry_at = None
        else:
            doc.status = "failed"
            doc.error = result.error[:2000]
            doc.next_retry_at = (
                timezone.now() + timezone.timedelta(seconds=backoff(doc.attempts)) if result.retryable else None
            )
        doc.save(
            update_fields=[
                "status",
                "is_fiscal",
                "external_id",
                "fiscal_number",
                "issued_at",
                "error",
                "next_retry_at",
                "response",
                "updated_at",
            ]
        )
    return doc


def retry(doc: FiscalDocument) -> FiscalDocument:
    if doc.status in TERMINAL:
        return doc
    doc.status = "queued"
    doc.next_retry_at = None
    doc.save(update_fields=["status", "next_retry_at", "updated_at"])
    from apps.fiscal import tasks

    enqueue(tasks.issue_document, str(doc.pk))
    return doc


def cancel(doc: FiscalDocument) -> FiscalDocument:
    if doc.status == "confirmed" and doc.is_fiscal:
        raise ValueError("A registered fiscal document cannot be cancelled; issue a refund instead.")
    doc.status = "cancelled"
    doc.save(update_fields=["status", "updated_at"])
    return doc


# ── rendering ─────────────────────────────────────────────────────────────


def render(doc: FiscalDocument) -> dict:
    """The receipt as data (extends apps.printing.receipts.receipt_data with the fiscal fields)."""
    from apps.printing.receipts import receipt_data

    profile = profile_for(doc.restaurant)
    order = doc.order
    base = (
        receipt_data(order, payment=doc.payment) if order is not None else {"lines": [], "totals": {}, "payments": []}
    )
    base.update(
        {
            "kind": doc.kind,
            "fiscal": doc.is_fiscal,
            "number": doc.fiscal_number,
            "external_id": doc.external_id,
            "issued_at": doc.issued_at.isoformat() if doc.issued_at else None,
            "vat_breakdown": doc.vat_breakdown,
            "vat_lines": doc.lines,
            "vat": {
                "rate": str(doc.vat_rate),
                "inclusive": doc.prices_include_vat,
                "net": str(doc.net_total),
                "vat": str(doc.vat_total),
                "gross": str(doc.gross_total),
            },
            "labels": {"vat_note": doc.payload.get("vat_label", "")},
            "footer": profile.receipt_footer,
        }
    )
    base.setdefault("restaurant", {})
    base["restaurant"].update(
        {
            "legal_name": profile.legal_name,
            "tax_id": profile.tax_id,
            "tax_id_line": f"ს/კ {profile.tax_id}" if profile.tax_id else "",
            "legal_address": profile.legal_address,
            "address": profile.legal_address or base["restaurant"].get("address", ""),
            "vat_payer": profile.vat_payer,
        }
    )
    return base
