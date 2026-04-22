"""
Shared builders for the three "initiate payment" flows — orders, reservations,
and session settlements — so each payment provider (BOG, Flitt) only owns its
own HTTP wire format, not the bookkeeping.

Each helper returns a small dataclass carrying every piece the provider needs
to build its payload: the persisted Order/Reservation, the charge amount, the
basket, and (for session settle) the list of covered orders. Providers are
free to build the provider-specific envelope around these shared primitives.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.conf import settings

from apps.menu.models import MenuItem
from apps.orders.models import Order, OrderItem, OrderItemModifier, OrderStatusHistory
from apps.reservations.models import Reservation
from apps.tables.models import Table, TableSession
from apps.tenants.models import Restaurant

logger = logging.getLogger(__name__)


# ── Return dataclasses ────────────────────────────────────────────────


@dataclass
class OrderInitiateResult:
    order: Order
    restaurant: Restaurant
    amount: Decimal
    basket: list[dict[str, Any]]


@dataclass
class ReservationInitiateResult:
    reservation: Reservation
    pre_order: Order | None
    restaurant: Restaurant
    amount: Decimal
    deposit: Decimal
    pre_order_total: Decimal
    basket: list[dict[str, Any]]


@dataclass
class SessionSettleInitiateResult:
    session: TableSession
    restaurant: Restaurant
    amount: Decimal
    basket: list[dict[str, Any]]
    covered_orders: list[Order] = field(default_factory=list)
    tip_amount: Decimal = Decimal("0")


# ── Basket helpers ────────────────────────────────────────────────────


def build_order_basket(order: Order) -> list[dict[str, Any]]:
    """Translate OrderItem rows into a provider-agnostic basket."""
    basket: list[dict[str, Any]] = []
    for item in order.items.all():
        basket.append(
            {
                "product_id": str(item.menu_item_id or item.id),
                "description": item.item_name,
                "quantity": item.quantity or 1,
                "unit_price": float(item.unit_price),
                "total_price": float(item.total_price),
            }
        )
    return basket


def build_reservation_basket(reservation: Reservation, deposit: Decimal) -> list[dict[str, Any]]:
    """One-line basket for reservation deposit; extended by caller for pre-order items."""
    return [
        {
            "product_id": f"deposit-{reservation.id}",
            "description": f"Reservation deposit — {reservation.confirmation_code}",
            "quantity": 1,
            "unit_price": float(deposit),
            "total_price": float(deposit),
        }
    ]


def _resolve_restaurant(slug: str) -> Restaurant:
    try:
        return Restaurant.objects.get(slug=slug, is_active=True)
    except Restaurant.DoesNotExist as exc:
        raise ValueError(f"Restaurant '{slug}' not found") from exc


# ── Order flow ────────────────────────────────────────────────────────


def create_pending_order(request, payload: dict[str, Any]) -> OrderInitiateResult:
    """
    Build an Order(pending_payment) from an OrderPayloadSerializer-validated
    payload. Does NOT talk to any payment provider — returns everything the
    caller needs to build the provider-specific request.

    Raises ``ValueError`` on input violations (unknown restaurant, ordering
    disabled, cross-restaurant menu items, etc.) — caller translates to 400.
    """
    restaurant = _resolve_restaurant(payload["restaurant_slug"])

    if not restaurant.accepts_remote_orders:
        raise ValueError("Ordering is disabled at this restaurant.")

    table = None
    if payload.get("table_id"):
        try:
            table = Table.objects.get(id=payload["table_id"], restaurant=restaurant)
        except Table.DoesNotExist as exc:
            raise ValueError("Table not found") from exc

    session_id = payload.get("table_session")
    if session_id:
        try:
            session_obj = TableSession.objects.get(id=session_id, table__restaurant=restaurant)
        except TableSession.DoesNotExist as exc:
            raise ValueError("Table session not found.") from exc
        if session_obj.status != "active":
            raise ValueError("This table session has ended. Scan a new QR to start over.")

    order = Order.objects.create(
        restaurant=restaurant,
        table=table,
        table_session_id=session_id,
        customer=request.user if request.user.is_authenticated else None,
        order_type=payload.get("order_type", "dine_in"),
        tip_amount=payload.get("tip_amount", 0),
        status="pending_payment",
        customer_name=payload.get("customer_name", ""),
        customer_phone=payload.get("customer_phone", ""),
        customer_email=payload.get("customer_email", ""),
        customer_notes=payload.get("customer_notes", ""),
        delivery_address=payload.get("delivery_address", ""),
    )

    for item_payload in payload["items"]:
        menu_item: MenuItem = item_payload["menu_item_id"]
        if menu_item.restaurant_id != restaurant.id:
            raise ValueError("One or more items don't belong to this restaurant.")

        order_item = OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            item_name=menu_item.safe_translation_getter("name", default=f"Item {menu_item.pk}"),
            item_description=menu_item.safe_translation_getter("description", default=""),
            unit_price=menu_item.price,
            quantity=item_payload.get("quantity", 1),
            total_price=menu_item.price * item_payload.get("quantity", 1),
            preparation_station=menu_item.preparation_station,
            special_instructions=item_payload.get("special_instructions", ""),
        )

        for modifier in item_payload.get("modifier_ids", []):
            OrderItemModifier.objects.create(
                order_item=order_item,
                modifier=modifier,
                modifier_name=modifier.safe_translation_getter("name", default=f"Modifier {modifier.pk}"),
                price_adjustment=modifier.price_adjustment,
            )

        order_item.recalculate_total()

    order.calculate_totals()

    # Apply a platform-loyalty tier discount when the customer is
    # authenticated, carries a tier with non-zero discount, and the
    # restaurant has opted into the program.
    try:
        from apps.loyalty.services import current_user_tier

        if request.user.is_authenticated and restaurant.accepts_platform_loyalty:
            tier = current_user_tier(request.user)
            if tier and tier.discount_percent > 0:
                pct = Decimal(tier.discount_percent) / Decimal(100)
                order.discount_amount = (order.subtotal * pct).quantize(Decimal("0.01"))
                order.calculate_totals()
    except Exception:
        logger.exception("Failed to apply platform loyalty discount")

    # Apply customer-requested wallet credit. We clamp to the actually-spendable
    # amount so over-asking on the frontend (stale balance, race) silently
    # downsizes rather than throwing — the user just pays the rest by card.
    _apply_wallet_to_order(request, order, payload.get("wallet_amount"))

    OrderStatusHistory.objects.create(
        order=order,
        from_status="",
        to_status="pending_payment",
        notes="Order awaiting payment confirmation.",
    )

    amount = Decimal(str(order.total))
    if amount <= 0:
        raise ValueError("Order total must be greater than zero.")

    return OrderInitiateResult(
        order=order,
        restaurant=restaurant,
        amount=amount,
        basket=build_order_basket(order),
    )


def _apply_wallet_to_order(request, order: Order, requested_amount) -> None:
    """
    Set ``order.wallet_applied`` to the spendable portion of the user's wallet,
    capped at ``order.subtotal − order.discount_amount`` so we never debit more
    than the pre-tax/tip/service line. Anonymous carts get ignored.

    The actual wallet debit is deferred until payment success — apps.payments
    success hooks call ``apps.referrals.services.spend_wallet`` once the funds
    are confirmed received.
    """
    if requested_amount in (None, "", 0, Decimal("0")):
        return
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return
    profile = getattr(request.user, "profile", None)
    if profile is None:
        return

    requested = Decimal(str(requested_amount))
    if requested <= 0:
        return

    balance = Decimal(profile.wallet_balance or 0)
    spendable_cap = Decimal(order.subtotal or 0) - Decimal(order.discount_amount or 0)
    spendable = min(requested, balance, spendable_cap)
    if spendable <= 0:
        return

    order.wallet_applied = spendable.quantize(Decimal("0.01"))
    order.save(update_fields=["wallet_applied", "updated_at"])
    order.calculate_totals()


# ── Reservation flow ──────────────────────────────────────────────────


def create_pending_reservation(request, payload: dict[str, Any]) -> ReservationInitiateResult:
    """
    Create a Reservation(pending_payment) + optional pre-order from payload.
    """
    restaurant = _resolve_restaurant(payload["restaurant_slug"])

    if not restaurant.accepts_reservations:
        raise ValueError("Reservations are disabled at this restaurant.")

    reservation = Reservation.objects.create(
        restaurant=restaurant,
        customer=request.user if request.user.is_authenticated else None,
        guest_name=payload["guest_name"],
        guest_phone=payload["guest_phone"],
        guest_email=payload.get("guest_email", ""),
        reservation_date=payload["reservation_date"],
        reservation_time=payload["reservation_time"],
        party_size=payload["party_size"],
        special_requests=payload.get("special_requests", ""),
        status="pending_payment",
        source="website",
    )

    pre_order: Order | None = None
    pre_order_total = Decimal("0")
    items = payload.get("items") or []
    wallet_amount_request = payload.get("wallet_amount")
    if items:
        pre_order = Order.objects.create(
            restaurant=restaurant,
            reservation=reservation,
            customer=reservation.customer,
            order_type="dine_in",
            status="pending_payment",
            customer_name=reservation.guest_name,
            customer_phone=reservation.guest_phone,
            customer_email=reservation.guest_email,
        )
        for item_payload in items:
            menu_item: MenuItem = item_payload["menu_item_id"]
            if menu_item.restaurant_id != restaurant.id:
                raise ValueError("Pre-order items must belong to the reservation's restaurant.")
            order_item = OrderItem.objects.create(
                order=pre_order,
                menu_item=menu_item,
                item_name=menu_item.safe_translation_getter("name", default=f"Item {menu_item.pk}"),
                item_description=menu_item.safe_translation_getter("description", default=""),
                unit_price=menu_item.price,
                quantity=item_payload.get("quantity", 1),
                total_price=menu_item.price * item_payload.get("quantity", 1),
                preparation_station=menu_item.preparation_station,
                special_instructions=item_payload.get("special_instructions", ""),
            )
            for modifier in item_payload.get("modifier_ids", []):
                OrderItemModifier.objects.create(
                    order_item=order_item,
                    modifier=modifier,
                    modifier_name=modifier.safe_translation_getter("name", default=f"Modifier {modifier.pk}"),
                    price_adjustment=modifier.price_adjustment,
                )
            order_item.recalculate_total()
        pre_order.calculate_totals()
        # Wallet credit applies only to the pre-order portion, not the deposit.
        _apply_wallet_to_order(request, pre_order, wallet_amount_request)
        OrderStatusHistory.objects.create(
            order=pre_order,
            from_status="",
            to_status="pending_payment",
            notes=f"Pre-order for reservation {reservation.confirmation_code}; awaiting payment.",
        )
        pre_order_total = Decimal(str(pre_order.total))

    override = payload.get("deposit_amount_override")
    if override is not None:
        deposit = Decimal(str(override))
    else:
        deposit = Decimal(settings.BOG_RESERVATION_DEPOSIT_AMOUNT)

    if deposit <= 0 and pre_order_total <= 0:
        raise ValueError("Reservation deposit must be greater than zero.")

    amount = deposit + pre_order_total
    basket = build_reservation_basket(reservation, deposit)
    if pre_order is not None:
        # Append each pre-order item so the provider-side receipt reflects
        # what the customer actually paid for.
        basket.extend(build_order_basket(pre_order))

    return ReservationInitiateResult(
        reservation=reservation,
        pre_order=pre_order,
        restaurant=restaurant,
        amount=amount,
        deposit=deposit,
        pre_order_total=pre_order_total,
        basket=basket,
    )
