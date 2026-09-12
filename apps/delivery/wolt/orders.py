"""Tolerant parsers for Wolt webhook notifications and ``GET /orders/{id}`` payloads (no DB access)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from apps.delivery.parsed import ParsedAttribute, ParsedOrder, ParsedProduct, minor_to_money, parse_dt


@dataclass
class Notification:
    id: str
    type: str
    order_id: str
    venue_id: str
    status: str
    resource_url: str
    created_at: str


def parse_notification(payload) -> Notification:
    p = payload if isinstance(payload, dict) else {}
    order = p.get("order") or {}
    return Notification(
        id=str(p.get("id", "")),
        type=str(p.get("type", "")),
        order_id=str(order.get("id", "")),
        venue_id=str(order.get("venue_id", "")),
        status=str(order.get("status", "")).upper(),
        resource_url=str(order.get("resource_url", "")),
        created_at=str(p.get("created_at", "")),
    )


def _amount(obj) -> Decimal:
    if isinstance(obj, dict):
        return minor_to_money(obj.get("amount"))
    return minor_to_money(obj)


def parse_order(payload: dict) -> ParsedOrder:
    p = payload or {}
    venue = p.get("venue") or {}
    delivery = p.get("delivery") or {}
    location = delivery.get("location") or {}
    coords = location.get("coordinates") or {}
    pre = p.get("pre_order") or {}
    products = []
    for it in p.get("items") or []:
        count = max(int(it.get("count") or 1), 1)
        options = []
        for o in it.get("options") or []:
            options.append(
                ParsedAttribute(
                    id=str(o.get("value_pos_id") or o.get("pos_id") or o.get("id") or ""),
                    name=" ".join(x for x in (str(o.get("name") or ""), str(o.get("value") or "")) if x).strip(),
                    price=_amount(o.get("price")),
                    quantity=max(int(o.get("count") or 1), 1),
                    line_id=str(o.get("id") or ""),
                )
            )
        base = _amount(it.get("base_price"))
        if not base and it.get("unit_price"):
            base = _amount(it.get("unit_price")) - sum(o.price * o.quantity for o in options)
        products.append(
            ParsedProduct(
                id=str(it.get("pos_id") or it.get("sku") or it.get("gtin") or ""),
                name=str(it.get("name") or ""),
                price=max(base, Decimal("0")),
                quantity=count,
                purchased_product_id=str(it.get("id") or ""),
                attributes=options,
                line_id=str(it.get("id") or ""),
            )
        )
    delivery_type = str(delivery.get("type") or "").lower()
    order_type = str(p.get("type") or "").lower()
    raw = json.dumps(p, ensure_ascii=False)
    total = _amount(p.get("price"))
    return ParsedOrder(
        order_id=str(p.get("id", "")),
        store_id=str(venue.get("id", "")),
        order_code=str(p.get("order_number") or ""),
        order_time=parse_dt(p.get("created_at")),
        estimated_pickup_time=parse_dt(p.get("pickup_eta")) or parse_dt(delivery.get("time")),
        is_picked_up_by_customer=delivery_type in ("takeaway", "eatin"),
        payment_method="CASH" if p.get("cash_payment") else "ONLINE",
        currency=str((p.get("price") or {}).get("currency") or ""),
        customer_name=str(p.get("consumer_name") or ""),
        customer_phone=str(p.get("consumer_phone_number") or ""),
        invoicing_details={"company_tax_id": p.get("company_tax_id")} if p.get("company_tax_id") else {},
        special_requirements=str(p.get("consumer_comment") or ""),
        products=products,
        estimated_total_price=total,
        total_customer_to_pay=total,
        delivery_address=str(location.get("formatted_address") or location.get("street_address") or ""),
        latitude=coords.get("lat"),
        longitude=coords.get("lon"),
        cutlery_requested=False,
        raw=raw[:16384],
        delivery_type=delivery_type,
        self_delivery=bool(delivery.get("self_delivery")),
        is_preorder=order_type == "preorder" or bool(pre.get("preorder_time")),
        preorder_time=parse_dt(pre.get("preorder_time")),
        platform_status=str(p.get("order_status") or "").lower(),
        extra={
            "is_wolt_plus": p.get("is_wolt_plus"),
            "is_no_contact_delivery": p.get("is_no_contact_delivery"),
            "loyalty_card_number": p.get("loyalty_card_number"),
            "delivery_fee": str(_amount(delivery.get("fee"))) if delivery.get("fee") else None,
        },
    )
