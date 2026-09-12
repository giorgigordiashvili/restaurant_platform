"""Platform-neutral view of an inbound order (what ``services.create_platform_order`` consumes)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal


def parse_dt(value):
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def minor_to_money(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return (Decimal(str(value)) / 100).quantize(Decimal("0.01"))


@dataclass
class ParsedAttribute:
    id: str
    name: str
    price: Decimal
    quantity: int = 1
    line_id: str = ""  # the platform's own id for the option line (Wolt refunds)


@dataclass
class ParsedProduct:
    id: str
    name: str
    price: Decimal
    quantity: int
    purchased_product_id: str = ""
    attributes: list[ParsedAttribute] = field(default_factory=list)
    line_id: str = ""  # the platform's own id for the item line (Wolt ``items[].id``)


@dataclass
class ParsedOrder:
    order_id: str
    store_id: str
    order_code: str = ""
    order_time: datetime | None = None
    estimated_pickup_time: datetime | None = None
    utc_offset_minutes: int = 0
    is_picked_up_by_customer: bool = False
    payment_method: str = ""
    currency: str = ""
    customer_name: str = ""
    customer_phone: str = ""
    customer_hash: str = ""
    invoicing_details: dict = field(default_factory=dict)
    courier_name: str = ""
    courier_phone: str = ""
    allergy_info: str = ""
    special_requirements: str = ""
    products: list[ParsedProduct] = field(default_factory=list)
    estimated_total_price: Decimal = Decimal("0")
    total_customer_to_pay: Decimal = Decimal("0")
    partner_discounted_products_total: Decimal = Decimal("0")
    delivery_address: str = ""
    latitude: float | None = None
    longitude: float | None = None
    pick_up_code: str = ""
    cutlery_requested: bool = False
    raw: str = ""
    # Wolt extras (Glovo leaves the defaults)
    delivery_type: str = ""  # homedelivery | takeaway | eatin
    self_delivery: bool = False
    is_preorder: bool = False
    preorder_time: datetime | None = None
    platform_status: str = ""
    extra: dict = field(default_factory=dict)
