"""
Print queue: render a document, wrap it in ESC/POS, hand it to the bridge.

Nothing here talks to a printer. Jobs are rows; the bridge claims them
(``claim_next``), reports ``mark_done`` / ``mark_failed``; a beat task puts
jobs a crashed bridge left in ``printing`` back in the queue.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.printing import render
from apps.printing.escpos import image_to_escpos
from apps.printing.models import Printer, PrintJob
from apps.printing.receipts import receipt_data

logger = logging.getLogger(__name__)

KITCHEN_STATUSES = ("confirmed", "preparing", "ready")
STALE_AFTER = timezone.timedelta(minutes=2)


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "printing_enabled", False))


def bridge_printers(restaurant, *, kinds=("kitchen", "bar")):
    return Printer.objects.filter(restaurant=restaurant, is_active=True, connection="bridge", kind__in=kinds)


def _create(
    printer, kind, image, *, order=None, payment=None, title="", payload=None, by=None, drawer=False
) -> PrintJob:
    job = PrintJob.objects.create(
        restaurant_id=printer.restaurant_id,
        printer=printer,
        kind=kind,
        order=order,
        payment=payment,
        title=title[:120],
        payload=payload or {},
        escpos=image_to_escpos(image, paper=printer.paper, cut=True, drawer=drawer),
        requested_by=by if getattr(by, "is_authenticated", False) else None,
    )
    return job


# ── enqueue ───────────────────────────────────────────────────────────────


def enqueue_ticket(order, *, reason: str = "new", items=None, by=None, printers=None) -> list[PrintJob]:
    """One ticket per kitchen/bar printer whose stations intersect the (given or live) items."""
    restaurant = order.restaurant
    if not enabled(restaurant):
        return []
    if items is None:
        items = list(order.items.exclude(status="cancelled").prefetch_related("modifiers").order_by("created_at"))
    else:
        items = list(items)
    if not items:
        return []
    if printers is None:
        printers = bridge_printers(restaurant)
    jobs = []
    for printer in printers:
        subset = [i for i in items if printer.serves(i.preparation_station)]
        if not subset:
            continue
        image = render.render_ticket(order, subset, printer=printer, reason=reason)
        jobs.append(
            _create(
                printer,
                "ticket",
                image,
                order=order,
                title=f"{order.order_number} · {reason}",
                payload={"reason": reason, "items": [str(i.pk) for i in subset], "station": printer.stations},
                by=by,
            )
        )
    return jobs


def enqueue_receipt(order, *, payment=None, by=None, printers=None, drawer=None) -> list[PrintJob]:
    restaurant = order.restaurant
    if not enabled(restaurant):
        return []
    if printers is None:
        printers = bridge_printers(restaurant, kinds=("receipt",))
    data = receipt_data(order, payment=payment)
    jobs = []
    for printer in printers:
        kick = (
            drawer if drawer is not None else bool(printer.open_drawer and payment and payment.payment_method == "cash")
        )
        image = render.render_receipt(data, printer=printer)
        jobs.append(
            _create(
                printer,
                "receipt",
                image,
                order=order,
                payment=payment,
                title=f"{order.order_number} · {data.get('number') or 'receipt'}",
                payload=data,
                by=by,
                drawer=kick,
            )
        )
    return jobs


def enqueue_report(shift, *, by=None, printers=None) -> list[PrintJob]:
    from apps.payments import services as ledger

    restaurant = shift.restaurant
    if not enabled(restaurant):
        return []
    report = shift.report if shift.status == "closed" and shift.report else ledger.x_report(shift)
    if printers is None:
        printers = bridge_printers(restaurant, kinds=("receipt",))
    jobs = []
    for printer in printers:
        image = render.render_z_report(shift, report, restaurant, printer=printer)
        jobs.append(
            _create(printer, "report", image, title=f"Shift #{shift.number}", payload={"shift": str(shift.pk)}, by=by)
        )
    return jobs


def enqueue_test(printer, *, by=None) -> PrintJob:
    image = render.render_test(printer, printer.restaurant)
    return _create(printer, "test", image, title="Test page", by=by, drawer=bool(printer.open_drawer))


# ── bridge side ───────────────────────────────────────────────────────────


def heartbeat(printer) -> None:
    Printer.objects.filter(pk=printer.pk).update(last_seen_at=timezone.now())


def claim_next(printer) -> PrintJob | None:
    """Oldest queued job for this printer, atomically moved to ``printing``."""
    heartbeat(printer)
    with transaction.atomic():
        job = (
            PrintJob.objects.select_for_update(skip_locked=True)
            .filter(printer=printer, status="queued")
            .order_by("created_at")
            .first()
        )
        if job is None:
            return None
        job.status = "printing"
        job.claimed_at = timezone.now()
        job.attempts += 1
        job.save(update_fields=["status", "claimed_at", "attempts", "updated_at"])
        return job


def mark_done(job) -> PrintJob:
    job.status = "done"
    job.printed_at = timezone.now()
    job.error = ""
    job.save(update_fields=["status", "printed_at", "error", "updated_at"])
    Printer.objects.filter(pk=job.printer_id).update(last_error="", last_seen_at=timezone.now())
    return job


def mark_failed(job, error: str = "") -> PrintJob:
    job.error = (error or "Print failed")[:300]
    job.status = "queued" if job.attempts < PrintJob.MAX_ATTEMPTS else "failed"
    job.claimed_at = None
    job.save(update_fields=["status", "error", "claimed_at", "updated_at"])
    Printer.objects.filter(pk=job.printer_id).update(last_error=job.error, last_seen_at=timezone.now())
    if job.status == "failed":
        from apps.notifications import hooks as notification_hooks

        notification_hooks.on_print_failed(job)
    return job


def retry(job) -> PrintJob:
    job.status = "queued"
    job.attempts = 0
    job.error = ""
    job.claimed_at = None
    job.save(update_fields=["status", "attempts", "error", "claimed_at", "updated_at"])
    return job


def requeue_stale() -> int:
    """Jobs a bridge claimed but never acknowledged (crash, power cut) go back to the queue."""
    cutoff = timezone.now() - STALE_AFTER
    stale = PrintJob.objects.filter(status="printing", claimed_at__lt=cutoff)
    n = 0
    for job in stale:
        mark_failed(job, "Bridge did not confirm the print")
        n += 1
    return n


def printer_status(restaurant) -> dict:
    printers = list(Printer.objects.filter(restaurant=restaurant, is_active=True))
    failed = PrintJob.objects.filter(restaurant=restaurant, status="failed").count()
    queued = PrintJob.objects.filter(restaurant=restaurant, status="queued").count()
    return {
        "printers": printers,
        "offline": [p for p in printers if p.connection == "bridge" and not p.is_online],
        "failed_jobs": failed,
        "queued_jobs": queued,
    }


def _money(v) -> Decimal:  # pragma: no cover - helper for templates
    return Decimal(v or 0)
