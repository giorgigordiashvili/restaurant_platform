"""Tolerant parser for Glovo order notifications (no DB access)."""

from __future__ import annotations

import json
from decimal import Decimal

from django.conf import settings

from apps.delivery.parsed import ParsedAttribute, ParsedOrder, ParsedProduct
from apps.delivery.parsed import parse_dt as _dt


def money_from_minor(value) -> Decimal:
    """Glovo sends integers in the smallest currency unit (per docs); ``GLOVO_PRICES_IN_MINOR_UNITS=False`` if not."""
    if value in (None, ""):
        return Decimal("0")
    d = Decimal(str(value))
    if getattr(settings, "GLOVO_PRICES_IN_MINOR_UNITS", True):
        return (d / 100).quantize(Decimal("0.01"))
    return d.quantize(Decimal("0.01"))


def parse_order(payload: dict) -> ParsedOrder:
    p = payload or {}
    customer = p.get("customer") or {}
    courier = p.get("courier") or {}
    address = p.get("delivery_address") or {}
    products = []
    for prod in p.get("products") or []:
        attrs = [
            ParsedAttribute(
                id=str(a.get("id", "")),
                name=str(a.get("name", "")),
                price=money_from_minor(a.get("price")),
                quantity=int(a.get("quantity") or 1),
            )
            for a in (prod.get("attributes") or [])
        ]
        products.append(
            ParsedProduct(
                id=str(prod.get("id", "")),
                name=str(prod.get("name", "")),
                price=money_from_minor(prod.get("price")),
                quantity=int(prod.get("quantity") or 1),
                purchased_product_id=str(prod.get("purchased_product_id", "")),
                attributes=attrs,
            )
        )
    raw = json.dumps(p, ensure_ascii=False)
    return ParsedOrder(
        order_id=str(p.get("order_id", "")),
        store_id=str(p.get("store_id", "")),
        order_code=str(p.get("order_code", "")),
        order_time=_dt(p.get("order_time")),
        estimated_pickup_time=_dt(p.get("estimated_pickup_time")),
        utc_offset_minutes=int(p.get("utc_offset_minutes") or 0),
        is_picked_up_by_customer=bool(p.get("is_picked_up_by_customer")),
        payment_method=str(p.get("payment_method", "")),
        currency=str(p.get("currency", "")),
        customer_name=str(customer.get("name", "")),
        customer_phone=str(customer.get("phone_number", "")),
        customer_hash=str(customer.get("hash", "")),
        invoicing_details=customer.get("invoicing_details") or {},
        courier_name=str(courier.get("name", "")),
        courier_phone=str(courier.get("phone_number", "")),
        allergy_info=str(p.get("allergy_info") or ""),
        special_requirements=str(p.get("special_requirements") or ""),
        products=products,
        estimated_total_price=money_from_minor(p.get("estimated_total_price")),
        total_customer_to_pay=money_from_minor(p.get("total_customer_to_pay")),
        partner_discounted_products_total=money_from_minor(p.get("partner_discounted_products_total")),
        delivery_address=str(address.get("label", "")),
        latitude=address.get("latitude"),
        longitude=address.get("longitude"),
        pick_up_code=str(p.get("pick_up_code", "")),
        cutlery_requested=bool(p.get("cutlery_requested")),
        raw=raw[:16384],
    )
