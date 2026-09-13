"""Never-raise entry points called from other apps' services."""

from __future__ import annotations

import logging

from django.urls import reverse

from apps.notifications import services

logger = logging.getLogger(__name__)


def _safe(fn):
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception("notification hook %s failed", fn.__name__)
            return None

    wrapper.__name__ = fn.__name__
    return wrapper


def _admin(name, **kwargs) -> str:
    try:
        return reverse(f"tenant_admin:{name}", kwargs=kwargs or None)
    except Exception:  # noqa: BLE001
        return ""


def _money(value) -> str:
    try:
        return f"{value:.2f}"
    except Exception:  # noqa: BLE001
        return str(value)


@_safe
def on_order_created(order, *, by=None) -> None:
    if order.source == "pos" or order.status == "pending_payment":
        return
    label = {"qr": "QR table order", "web": "Online order"}.get(order.source, f"{order.source.title()} order")
    where = f"table {order.table.number}" if getattr(order, "table", None) else order.get_order_type_display()
    services.notify(
        order.restaurant,
        "order.new",
        title=f"{label} #{order.order_number}",
        body=f"{where} · {_money(order.total)} {getattr(order.restaurant, 'default_currency', 'GEL')}",
        data={"kind": "order", "id": str(order.pk)},
        url=_admin("orders_order_change", object_id=order.pk),
        dedupe_key=f"order.new:{order.pk}",
    )


@_safe
def on_order_cancelled(order, *, by=None) -> None:
    if order.status != "cancelled" or not getattr(order, "was_accepted", True):
        return
    actor = getattr(by, "get_full_name", lambda: "")() or getattr(by, "email", "") or "customer / platform"
    services.notify(
        order.restaurant,
        "order.cancelled",
        title=f"Order #{order.order_number} cancelled",
        body=f"{order.cancellation_reason or ''} — {actor}".strip(" —"),
        data={"kind": "order", "id": str(order.pk)},
        url=_admin("orders_order_change", object_id=order.pk),
        dedupe_key=f"order.cancelled:{order.pk}",
    )


@_safe
def on_reservation_created(reservation) -> None:
    services.notify(
        reservation.restaurant,
        "reservation.new",
        title=f"Reservation {reservation.reservation_date:%d.%m} {reservation.reservation_time:%H:%M}",
        body=f"{reservation.guest_name} · {reservation.party_size} guests · {reservation.guest_phone}",
        data={"kind": "reservation", "id": str(reservation.pk)},
        url=_admin("reservations_reservation_change", object_id=reservation.pk),
        dedupe_key=f"reservation.new:{reservation.pk}",
    )


@_safe
def on_reservation_confirmed(reservation) -> None:
    services.reservation_confirmed(reservation)


@_safe
def on_reservation_cancelled(reservation, *, by=None) -> None:
    services.notify(
        reservation.restaurant,
        "reservation.cancelled",
        title=f"Reservation cancelled: {reservation.guest_name}",
        body=f"{reservation.reservation_date:%d.%m} {reservation.reservation_time:%H:%M} · {reservation.party_size} guests"
        + (f" · {reservation.cancellation_reason}" if reservation.cancellation_reason else ""),
        data={"kind": "reservation", "id": str(reservation.pk)},
        url=_admin("reservations_reservation_change", object_id=reservation.pk),
        dedupe_key=f"reservation.cancelled:{reservation.pk}",
    )


@_safe
def on_inventory_alert(alert) -> None:
    code = {"low_stock": "stock.low", "out_of_stock": "stock.out", "negative_stock": "stock.low"}.get(alert.kind)
    if not code:
        return
    services.notify(
        alert.restaurant,
        code,
        title=alert.message[:200],
        body="",
        data={"kind": "inventory_alert", "id": str(alert.pk)},
        url=_admin("inventory_warehouseoverview_changelist"),
        dedupe_key=f"{code}:{alert.dedupe_key}",
    )


@_safe
def on_print_failed(job) -> None:
    if job.status != "failed":
        return
    services.notify(
        job.restaurant,
        "print.failed",
        title=f"Printer {job.printer.name}: {job.get_kind_display()} failed",
        body=job.error[:180],
        data={"kind": "print_job", "id": str(job.pk)},
        url=_admin("printing_printjob_changelist"),
        dedupe_key=f"print.failed:{job.printer_id}",
    )


@_safe
def on_shift_closed(shift, *, by=None) -> None:
    report = shift.report or {}
    sales = report.get("sales", {}) if isinstance(report, dict) else {}
    body = f"Expected {_money(shift.expected_cash)}, counted {_money(shift.counted_cash)}, difference {_money(shift.difference)}"
    if isinstance(sales, dict) and sales.get("net") is not None:
        body = f"Sales {sales.get('net')} · " + body
    services.notify(
        shift.restaurant,
        "shift.closed",
        title=f"Shift #{shift.number} closed",
        body=body,
        data={"kind": "shift", "id": str(shift.pk)},
        url=_admin("payments_cashshift_change", object_id=shift.pk),
        dedupe_key=f"shift.closed:{shift.pk}",
    )


@_safe
def on_review_created(review) -> None:
    services.notify(
        review.restaurant,
        "review.new",
        title=f"{'★' * int(review.rating)} new review",
        body=(review.title or review.body or "")[:180],
        data={"kind": "review", "id": str(review.pk)},
        url=_admin("reviews_review_change", object_id=review.pk),
        dedupe_key=f"review.new:{review.pk}",
    )


@_safe
def on_delivery_cancel_requested(order) -> None:
    services.notify(
        order.restaurant,
        "delivery.cancel_requested",
        title=f"Call {order.source.title()} to cancel #{order.order_number}",
        body="The platform cannot be cancelled through the API after acceptance.",
        data={"kind": "order", "id": str(order.pk)},
        url=_admin("delivery_deliveryplatformspage_changelist"),
        dedupe_key=f"delivery.cancel_requested:{order.pk}",
    )


@_safe
def on_delivery_failed(delivery) -> None:
    order = delivery.order
    services.notify(
        order.restaurant,
        "delivery.failed",
        title=f"Courier failed for #{order.order_number}",
        body=(delivery.error or "The courier platform rejected the request.")[:140],
        data={"kind": "order", "id": str(order.pk)},
        url=_admin("orders_order_change", object_id=order.pk),
        dedupe_key=f"delivery.failed:{delivery.pk}",
    )


@_safe
def on_module_toggled(restaurant, enabled, *, by=None) -> None:
    if enabled:
        services.settings_for(restaurant)
