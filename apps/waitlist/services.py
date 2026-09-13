"""Today's queue: add, quote, notify (SMS), seat into a table session, expire."""

from __future__ import annotations

import logging
from datetime import timedelta
from statistics import median

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.tenants import hours as H
from apps.waitlist.models import WaitlistEntry, WaitlistSettings

logger = logging.getLogger(__name__)


class WaitlistError(Exception):
    def __init__(self, code: str, message: str = "", **extra):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.extra = extra


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "waitlist_enabled", False))


def settings_for(restaurant) -> WaitlistSettings:
    row, _created = WaitlistSettings.objects.get_or_create(restaurant=restaurant)
    return row


def today(restaurant):
    return H.local_now(restaurant).date()


def queue(restaurant, day=None):
    day = day or today(restaurant)
    return (
        WaitlistEntry.objects.filter(restaurant=restaurant, date=day)
        .select_related("table", "session", "reservation")
        .order_by("position", "created_at")
    )


def open_queue(restaurant, day=None):
    return queue(restaurant, day).filter(status__in=WaitlistEntry.OPEN)


def status_url(entry) -> str:
    base = getattr(settings, "FRONTEND_BASE_URL", "https://aimenu.ge").rstrip("/")
    return f"{base}/w/status/{entry.token}"


def join_url(restaurant) -> str:
    base = getattr(settings, "FRONTEND_BASE_URL", "https://aimenu.ge").rstrip("/")
    return f"{base}/w/{restaurant.slug}/{settings_for(restaurant).join_token}"


# ── estimates ──────────────────────────────────────────────────────────────


def turn_minutes(restaurant, party_size: int) -> int | None:
    """Median length of recent table sessions on tables that fit the party (last 30 days)."""
    from apps.tables.models import TableSession

    since = timezone.now() - timedelta(days=30)
    rows = TableSession.objects.filter(
        table__restaurant=restaurant,
        status="closed",
        closed_at__isnull=False,
        started_at__gte=since,
        table__capacity__gte=party_size,
    ).values_list("started_at", "closed_at")[:200]
    mins = [
        (closed - started).total_seconds() / 60
        for started, closed in rows
        if closed and 10 <= (closed - started).total_seconds() / 60 <= 240
    ]
    if len(mins) < 3:
        return None
    return int(median(mins))


def estimate_wait(restaurant, party_size: int) -> int:
    """
    Quoted minutes: parties ahead in the queue that need a table of this size,
    spread over the tables that fit, times a typical turn. Falls back to the
    configured default.
    """
    from apps.tables.models import Table

    cfg = settings_for(restaurant)
    fitting = Table.objects.filter(restaurant=restaurant, is_active=True, capacity__gte=party_size)
    free = fitting.filter(status="available").count()
    ahead = open_queue(restaurant).filter(party_size__lte=party_size).count()
    if free > ahead:
        return max(int(cfg.default_wait_minutes) // 3, 5)
    turn = turn_minutes(restaurant, party_size) or int(cfg.default_wait_minutes) * 3
    tables = max(fitting.count(), 1)
    waves = (ahead - free) // tables + 1
    return max(int(cfg.default_wait_minutes), min(waves * turn // 2, 180))


# ── mutations ──────────────────────────────────────────────────────────────


def _next_position(restaurant, day) -> int:
    top = WaitlistEntry.objects.filter(restaurant=restaurant, date=day).aggregate(m=Max("position"))["m"]
    return (top or 0) + 1


def add_entry(
    restaurant,
    *,
    name: str,
    party_size: int,
    phone: str = "",
    quoted: int | None = None,
    source: str = "pos",
    by=None,
    notes: str = "",
    reservation=None,
) -> WaitlistEntry:
    cfg = settings_for(restaurant)
    name = (name or "").strip()
    if not name:
        raise WaitlistError("name_required", "A name is required.")
    party_size = max(int(party_size or 1), 1)
    if party_size > cfg.max_party_size:
        raise WaitlistError("party_too_big", f"Parties of up to {cfg.max_party_size} can join the waitlist.")
    if source == "self" and not cfg.allow_self_join:
        raise WaitlistError("self_join_off", "Joining from your phone is switched off.")
    from apps.notifications.providers import normalize_phone

    phone = normalize_phone(phone) if phone else ""
    if source == "self" and phone and open_queue(restaurant).filter(phone=phone).exists():
        raise WaitlistError("already_waiting", "You are already on the list.")
    quoted = int(quoted) if quoted is not None else estimate_wait(restaurant, party_size)
    day = today(restaurant)
    with transaction.atomic():
        entry = WaitlistEntry.objects.create(
            restaurant=restaurant,
            date=day,
            position=_next_position(restaurant, day),
            name=name[:120],
            phone=phone,
            party_size=party_size,
            quoted_minutes=quoted,
            source=source,
            notes=(notes or "")[:200],
            added_by=by if getattr(by, "is_authenticated", False) else None,
            reservation=reservation,
            estimated_ready_at=timezone.now() + timedelta(minutes=quoted),
        )
    if cfg.sms_on_join and phone:
        _message(entry, "waitlist_joined", by=by)
    if source == "self":
        from apps.notifications import hooks as notification_hooks

        notification_hooks.on_waitlist_self_joined(entry)
    return entry


def update_entry(entry: WaitlistEntry, *, by=None, **fields) -> WaitlistEntry:
    allowed = {"name", "phone", "party_size", "notes", "quoted_minutes"}
    changed = []
    for k, v in fields.items():
        if k in allowed and v is not None:
            if k == "phone":
                from apps.notifications.providers import normalize_phone

                v = normalize_phone(v) if v else ""
            setattr(entry, k, v)
            changed.append(k)
    if "quoted_minutes" in changed:
        entry.estimated_ready_at = entry.created_at + timedelta(minutes=int(entry.quoted_minutes))
        changed.append("estimated_ready_at")
    if changed:
        entry.save(update_fields=[*changed, "updated_at"])
    return entry


def reorder(entry: WaitlistEntry, position: int) -> None:
    """Move an entry to ``position`` (1-based) among today's open entries and resequence."""
    rows = list(open_queue(entry.restaurant, entry.date).exclude(pk=entry.pk))
    position = max(1, min(int(position), len(rows) + 1))
    rows.insert(position - 1, entry)
    for i, row in enumerate(rows, start=1):
        if row.position != i:
            WaitlistEntry.objects.filter(pk=row.pk).update(position=i)


def notify_ready(entry: WaitlistEntry, *, by=None) -> WaitlistEntry:
    if entry.status not in WaitlistEntry.OPEN:
        raise WaitlistError("not_open", "This party is no longer waiting.")
    entry.status = "notified"
    entry.notified_at = timezone.now()
    entry.notify_count += 1
    entry.save(update_fields=["status", "notified_at", "notify_count", "updated_at"])
    cfg = settings_for(entry.restaurant)
    if cfg.sms_on_ready and entry.phone:
        _message(entry, "table_ready", by=by)
    _audit(entry, by, "waitlist_notified", "Table ready sent")
    return entry


def seat(entry: WaitlistEntry, table, *, by=None):
    from apps.tables.services import open_session

    if entry.status not in WaitlistEntry.OPEN:
        raise WaitlistError("not_open", "This party is no longer waiting.")
    if table.restaurant_id != entry.restaurant_id:
        raise WaitlistError("table_not_found", "Table not found.")
    with transaction.atomic():
        session = open_session(table, by, party_size=entry.party_size)
        entry.status = "seated"
        entry.seated_at = timezone.now()
        entry.table = table
        entry.session = session
        entry.save(update_fields=["status", "seated_at", "table", "session", "updated_at"])
        if entry.reservation_id and entry.reservation.status in ("pending", "confirmed", "waitlist"):
            entry.reservation.mark_seated(table=table, by=by)
    _resequence(entry.restaurant, entry.date)
    _audit(entry, by, "waitlist_seated", f"Seated at table {table.number}")
    return session


def mark(entry: WaitlistEntry, status: str, *, by=None) -> WaitlistEntry:
    if status not in ("left", "cancelled", "no_show"):
        raise WaitlistError("bad_status", "Unknown status.")
    if entry.status not in WaitlistEntry.OPEN:
        raise WaitlistError("not_open", "This party is no longer waiting.")
    entry.status = status
    entry.left_at = timezone.now()
    entry.save(update_fields=["status", "left_at", "updated_at"])
    _resequence(entry.restaurant, entry.date)
    return entry


def from_reservation(reservation, *, by=None) -> WaitlistEntry:
    """A waitlisted (or pending / confirmed) reservation becomes today's queue entry."""
    if reservation.status not in ("waitlist", "pending", "confirmed"):
        raise WaitlistError("bad_status", "Only open reservations can join today's queue.")
    existing = WaitlistEntry.objects.filter(reservation=reservation, status__in=WaitlistEntry.OPEN).first()
    if existing:
        return existing
    return add_entry(
        reservation.restaurant,
        name=reservation.guest_name,
        phone=reservation.guest_phone,
        party_size=reservation.party_size,
        source="reservation",
        by=by,
        notes=(reservation.special_requests or "")[:200],
        reservation=reservation,
    )


def _resequence(restaurant, day) -> None:
    for i, row in enumerate(open_queue(restaurant, day), start=1):
        if row.position != i:
            WaitlistEntry.objects.filter(pk=row.pk).update(position=i)


# ── beat ───────────────────────────────────────────────────────────────────


def auto_expire() -> int:
    """Notified guests who did not turn up in time become no-shows."""
    n = 0
    now = timezone.now()
    for entry in WaitlistEntry.objects.filter(status="notified").select_related("restaurant"):
        minutes = settings_for(entry.restaurant).notify_expire_minutes
        if entry.notified_at and entry.notified_at + timedelta(minutes=minutes) < now:
            mark(entry, "no_show")
            n += 1
    return n


def daily_close() -> int:
    """Leftover open entries from earlier service days are closed as 'left'."""
    n = 0
    for entry in WaitlistEntry.objects.filter(status__in=WaitlistEntry.OPEN).select_related("restaurant"):
        if entry.date < today(entry.restaurant):
            mark(entry, "left")
            n += 1
    return n


# ── reporting ──────────────────────────────────────────────────────────────


def report(restaurant, start_date, end_date) -> dict:
    qs = WaitlistEntry.objects.filter(restaurant=restaurant, date__gte=start_date, date__lte=end_date)
    total = qs.count()
    seated = list(qs.filter(status="seated").values_list("created_at", "seated_at", "quoted_minutes"))
    waits = [int((s - c).total_seconds() // 60) for c, s, _q in seated if s]
    quoted = [q for _c, _s, q in seated]
    abandoned = qs.filter(status__in=("left", "no_show", "cancelled")).count()
    return {
        "walk_ins": total,
        "seated": len(seated),
        "abandoned": abandoned,
        "abandon_rate": round(abandoned * 100 / total, 1) if total else 0,
        "avg_wait": round(sum(waits) / len(waits), 1) if waits else 0,
        "avg_quoted": round(sum(quoted) / len(quoted), 1) if quoted else 0,
        "self_joined": qs.filter(source="self").count(),
    }


def summary(restaurant) -> dict:
    rows = list(open_queue(restaurant))
    waits = [e.waited_minutes for e in rows]
    seated_today = queue(restaurant).filter(status="seated").count()
    return {
        "waiting": len(rows),
        "notified": sum(1 for e in rows if e.status == "notified"),
        "longest_wait": max(waits) if waits else 0,
        "seated_today": seated_today,
    }


# ── helpers ────────────────────────────────────────────────────────────────


def _message(entry: WaitlistEntry, kind: str, *, by=None) -> None:
    try:
        from apps.notifications import services as notifications

        r = entry.restaurant
        if not notifications.enabled(r):
            return
        cfg = notifications.settings_for(r)
        lang = getattr(r, "default_language", "ka") or "ka"
        text = notifications.render(
            cfg.template(kind, lang),
            name=entry.name,
            restaurant=r.name,
            guests=entry.party_size,
            minutes=entry.quoted_minutes,
            position=entry.position,
            link=status_url(entry),
        )
        notifications.send_message(r, "sms", entry.phone, text, kind=kind, ref=entry, by=by)
    except Exception:  # noqa: BLE001 - never breaks the queue
        logger.exception("waitlist message %s failed for %s", kind, entry.pk)


def _audit(entry, by, action, description) -> None:
    try:
        from apps.audit.services import log_action

        log_action(
            action,
            user=by if getattr(by, "is_authenticated", False) else None,
            restaurant=entry.restaurant,
            description=f"{entry.name} ({entry.party_size}): {description}",
            target_model="WaitlistEntry",
            target_id=str(entry.pk),
        )
    except Exception:  # pragma: no cover
        logger.exception("waitlist audit failed")
