from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from apps.delivery.errors import PlatformClientError


class CourierError(PlatformClientError):
    pass


@dataclass
class QuoteResult:
    price: Decimal
    currency: str = "GEL"
    promise_id: str = ""
    pickup_eta: datetime | None = None
    dropoff_eta: datetime | None = None
    valid_until: datetime | None = None
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "price": str(self.price),
            "currency": self.currency,
            "promise_id": self.promise_id,
            "pickup_eta": self.pickup_eta.isoformat() if self.pickup_eta else None,
            "dropoff_eta": self.dropoff_eta.isoformat() if self.dropoff_eta else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
        }


@dataclass
class CreatedResult:
    external_id: str
    status: str = "requested"  # requested | accepted | assigned
    tracking_url: str = ""
    cost: Decimal | None = None
    pickup_eta: datetime | None = None
    dropoff_eta: datetime | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class StatusResult:
    status: str  # Delivery.STATUS_CHOICES value
    courier_name: str = ""
    courier_phone: str = ""
    pickup_eta: datetime | None = None
    dropoff_eta: datetime | None = None
    tracking_url: str = ""
    lat: float | None = None
    lng: float | None = None
    raw: dict = field(default_factory=dict)


class CourierProvider:
    code = ""
    needs_network = True

    def __init__(self, link=None, *, session=None):
        self.link = link
        self.session = session

    def quote(self, delivery) -> QuoteResult:  # pragma: no cover - interface
        raise NotImplementedError

    def create(self, delivery) -> CreatedResult:  # pragma: no cover - interface
        raise NotImplementedError

    def cancel(self, delivery, reason: str = "") -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def refresh(self, delivery) -> StatusResult | None:  # pragma: no cover - interface
        return None


def recipient_of(order) -> dict:
    return {
        "name": (order.customer_name or "Guest")[:100],
        "phone": order.customer_phone or "",
        "email": order.customer_email or "",
    }


def parcels_of(order) -> list[dict]:
    return [
        {
            "description": f"{item.quantity} × {item.item_name}"[:100],
            "identifier": str(item.pk),
            "count": int(item.quantity or 1),
        }
        for item in order.items.exclude(status="cancelled")
    ]
