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

from apps.inventory import hooks as inventory_hooks
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
    """Translate OrderItem rows (+ delivery / packaging fee lines) into a provider-agnostic basket."""
    basket: list[dict[str, Any]] = []
    for item in order.items.exclude(status="cancelled"):
        basket.append(
            {
                "product_id": str(item.menu_item_id or item.id),
                "description": item.item_name,
                "quantity": item.quantity or 1,
                "unit_price": float(item.unit_price),
                "total_price": float(item.total_price),
            }
        )
    for key, label in (("delivery_fee", "Delivery"), ("packaging_fee", "Packaging")):
        amount = Decimal(getattr(order, key, 0) or 0)
        if amount > 0:
            basket.append(
                {
                    "product_id": key,
                    "description": label,
                    "quantity": 1,
                    "unit_price": float(amount),
                    "total_price": float(amount),
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
    caller needs to build the provider-specific request. Used by BOG and Flitt.

    Raises ``ValueError`` on input violations (unknown restaurant, ordering
    disabled, closed / out of zone, cross-restaurant menu items, promo code
    problems ...) — caller translates to 400.
    """
    from apps.notifications import hooks as notification_hooks
    from apps.ordering import services as ordering_services
    from apps.promotions import hooks as promotion_hooks

    restaurant = _resolve_restaurant(payload["restaurant_slug"])

    if not restaurant.accepts_remote_orders:
        raise ValueError("Ordering is disabled at this restaurant.")
    order_type = payload.get("order_type", "dine_in")
    if order_type != "dine_in" and not restaurant.accepts_takeaway:
        raise ValueError("This restaurant only takes dine-in orders.")

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
        if table is None:
            table = session_obj.table

    order = Order.objects.create(
        restaurant=restaurant,
        table=table,
        table_session_id=session_id,
        customer=request.user if request.user.is_authenticated else None,
        order_type=order_type,
        tip_amount=payload.get("tip_amount", 0),
        status="pending_payment",
        customer_name=payload.get("customer_name", ""),
        customer_phone=payload.get("customer_phone", ""),
        customer_email=payload.get("customer_email", ""),
        customer_notes=payload.get("customer_notes", ""),
        delivery_address=payload.get("delivery_address", ""),
        source="qr" if session_id else "web",
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
            if modifier.group.restaurant_id != restaurant.id:
                raise ValueError("One or more modifiers don't belong to this restaurant.")
            OrderItemModifier.objects.create(
                order_item=order_item,
                modifier=modifier,
                modifier_name=modifier.safe_translation_getter("name", default=f"Modifier {modifier.pk}"),
                price_adjustment=modifier.price_adjustment,
            )

        order_item.recalculate_total()

    order.calculate_totals()

    # Pickup / delivery rules: hours, slots, zones, fees, minimums (raises FulfilmentError -> ValueError).
    if order_type != "dine_in":
        fulfilment = ordering_services.validate_fulfilment(
            restaurant,
            order_type,
            subtotal=order.subtotal,
            scheduled_for=payload.get("scheduled_for"),
            lat=payload.get("lat"),
            lng=payload.get("lng"),
            address=payload.get("delivery_address", ""),
            address_json=payload.get("address") or {},
            instructions=payload.get("delivery_instructions", ""),
        )
        ordering_services.apply_fulfilment(order, fulfilment)
        order.calculate_totals()

    # Happy hours / promo codes before stock, like the cash order path.
    promotion_hooks.on_order_items_changed(order, channel=order.source)
    if payload.get("promo_code"):
        from apps.promotions import services as promotion_services

        try:
            promotion_services.redeem_code(order, payload["promo_code"], by=request.user, channel=order.source)
        except promotion_services.PromotionError as exc:
            raise ValueError(exc.message) from exc

    # Hold the ingredients now (InsufficientStock -> 409, order rolls back).
    inventory_hooks.on_order_created(order)
    notification_hooks.on_order_created(order)
    try:
        from apps.crm import hooks as crm_hooks

        crm_hooks.on_order_created(order, consent=bool(payload.get("marketing_opt_in")))
    except Exception:  # pragma: no cover - never blocks checkout
        logger.exception("CRM hook failed")

    # Platform-loyalty tier discount (authenticated customer, restaurant opted in).
    try:
        from apps.loyalty.services import current_user_tier
        from apps.orders.services import apply_loyalty_tier_discount

        if request.user.is_authenticated and restaurant.accepts_platform_loyalty:
            apply_loyalty_tier_discount(order, current_user_tier(request.user))
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
                if modifier.group.restaurant_id != restaurant.id:
                    raise ValueError("One or more modifiers don't belong to this restaurant.")
                OrderItemModifier.objects.create(
                    order_item=order_item,
                    modifier=modifier,
                    modifier_name=modifier.safe_translation_getter("name", default=f"Modifier {modifier.pk}"),
                    price_adjustment=modifier.price_adjustment,
                )
            order_item.recalculate_total()
        pre_order.calculate_totals()
        inventory_hooks.on_order_created(pre_order)
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
