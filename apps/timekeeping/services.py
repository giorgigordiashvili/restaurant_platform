"""Clock in / out, who's in, hours per employee, rota weeks (publish / copy / reminders)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.reports.periods import restaurant_tz
from apps.staff.models import StaffMember
from apps.timekeeping.models import RotaShift, TimeEntry

ZERO = Decimal("0")
STALE_AFTER = timedelta(hours=16)


class TimekeepingError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "timekeeping_enabled", False))


def member_for(user, restaurant) -> StaffMember | None:
    return (
        StaffMember.objects.filter(user=user, restaurant=restaurant, is_active=True)
        .select_related("user", "role")
        .first()
    )


# ── clock ─────────────────────────────────────────────────────────────────


def open_entry(member) -> TimeEntry | None:
    return TimeEntry.objects.filter(staff_member=member, clock_out__isnull=True).order_by("-clock_in").first()


def clock_in(member, *, by=None, source="pos", note="") -> TimeEntry:
    if open_entry(member) is not None:
        raise TimekeepingError("already_in", _("Already clocked in."))
    entry = TimeEntry.objects.create(
        restaurant=member.restaurant, staff_member=member, clock_in=timezone.now(), source=source, note=note[:200]
    )
    _audit("clock_in", entry, by, f"{member.user.get_full_name() or member.user.email} clocked in")
    return entry


def clock_out(member, *, by=None, break_minutes: int = 0, note="") -> TimeEntry:
    entry = open_entry(member)
    if entry is None:
        raise TimekeepingError("not_in", _("Not clocked in."))
    entry.clock_out = timezone.now()
    entry.break_minutes = max(int(break_minutes or 0), 0)
    if note:
        entry.note = note[:200]
    entry.save(update_fields=["clock_out", "break_minutes", "note", "updated_at"])
    _audit(
        "clock_out",
        entry,
        by,
        f"{member.user.get_full_name() or member.user.email} clocked out ({entry.worked_hours} h)",
    )
    return entry


def whos_in(restaurant) -> list[TimeEntry]:
    return list(
        TimeEntry.objects.filter(restaurant=restaurant, clock_out__isnull=True)
        .select_related("staff_member__user", "staff_member__role")
        .order_by("clock_in")
    )


def today_minutes(member, now=None) -> int:
    now = now or timezone.now()
    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    total = 0
    for e in TimeEntry.objects.filter(staff_member=member, clock_in__gte=start):
        total += e.worked_minutes
    return total


def auto_close_stale(now=None) -> int:
    """Forgotten clock-outs: close after 16 h and flag the entry for a manager to correct."""
    now = now or timezone.now()
    n = 0
    for e in TimeEntry.objects.filter(clock_out__isnull=True, clock_in__lt=now - STALE_AFTER):
        e.clock_out = e.clock_in + STALE_AFTER
        e.auto_closed = True
        e.source = "auto"
        e.save(update_fields=["clock_out", "auto_closed", "source", "updated_at"])
        n += 1
    return n


# ── hours report ──────────────────────────────────────────────────────────


def hours_report(restaurant, start, end) -> list[dict]:
    """Per employee: worked hours, entries, labour cost (hourly_rate) and scheduled hours in [start, end)."""
    members = {m.pk: m for m in StaffMember.objects.filter(restaurant=restaurant).select_related("user", "role")}
    rows = {}
    for e in TimeEntry.objects.filter(restaurant=restaurant, clock_in__gte=start, clock_in__lt=end):
        row = rows.setdefault(e.staff_member_id, {"minutes": 0, "entries": 0, "auto_closed": 0, "scheduled": ZERO})
        row["minutes"] += e.worked_minutes
        row["entries"] += 1
        row["auto_closed"] += int(e.auto_closed)
    for s in RotaShift.objects.filter(
        restaurant=restaurant, date__gte=start.date(), date__lt=end.date(), published=True
    ):
        row = rows.setdefault(s.staff_member_id, {"minutes": 0, "entries": 0, "auto_closed": 0, "scheduled": ZERO})
        row["scheduled"] += s.hours
    out = []
    for member_id, row in rows.items():
        m = members.get(member_id)
        if m is None:
            continue
        hours = (Decimal(row["minutes"]) / Decimal(60)).quantize(Decimal("0.01"))
        rate = getattr(m, "hourly_rate", None) or ZERO
        out.append(
            {
                "member_id": str(member_id),
                "name": m.user.get_full_name() or m.user.email,
                "role": m.role.get_display_name() if m.role_id else "",
                "hours": hours,
                "entries": row["entries"],
                "auto_closed": row["auto_closed"],
                "scheduled": row["scheduled"],
                "variance": (hours - row["scheduled"]).quantize(Decimal("0.01")),
                "rate": rate,
                "cost": (hours * rate).quantize(Decimal("0.01")),
            }
        )
    out.sort(key=lambda r: (-r["hours"], r["name"]))
    return out


# ── rota ──────────────────────────────────────────────────────────────────


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_shifts(restaurant, monday: date):
    return (
        RotaShift.objects.filter(restaurant=restaurant, date__gte=monday, date__lt=monday + timedelta(days=7))
        .select_related("staff_member__user", "staff_member__role")
        .order_by("date", "start_time")
    )


def publish_week(restaurant, monday: date, *, by=None) -> int:
    """Mark the week's shifts published and tell each person who has one."""
    from apps.notifications import services as notifications

    shifts = list(week_shifts(restaurant, monday))
    if not shifts:
        return 0
    now = timezone.now()
    per_member: dict = {}
    with transaction.atomic():
        for s in shifts:
            if not s.published:
                s.published = True
                s.published_at = now
                s.save(update_fields=["published", "published_at", "updated_at"])
            per_member.setdefault(s.staff_member_id, []).append(s)
    for member_id, rows in per_member.items():
        member = rows[0].staff_member
        days = ", ".join(f"{s.date:%a %d.%m} {s.start_time:%H:%M}–{s.end_time:%H:%M}" for s in rows[:7])
        notifications.notify(
            restaurant,
            "rota.published",
            title=_("Your shifts for the week of %(date)s") % {"date": f"{monday:%d.%m}"},
            body=days,
            data={"kind": "rota", "week": monday.isoformat()},
            dedupe_key=f"rota.published:{monday}:{member_id}:{now:%Y%m%d%H}",
            users=[member.user],
        )
    _audit_plain("rota_publish", restaurant, by, f"Rota published for the week of {monday}")
    return len(shifts)


def copy_week(restaurant, from_monday: date, to_monday: date, *, by=None) -> int:
    """Duplicate one week's shifts into another (unpublished); skips people who already have a shift that day."""
    existing = {(s.staff_member_id, s.date) for s in week_shifts(restaurant, to_monday)}
    n = 0
    for s in week_shifts(restaurant, from_monday):
        target = s.date + (to_monday - from_monday)
        if (s.staff_member_id, target) in existing or not s.staff_member.is_active:
            continue
        RotaShift.objects.create(
            restaurant=restaurant,
            staff_member=s.staff_member,
            date=target,
            start_time=s.start_time,
            end_time=s.end_time,
            label=s.label,
            note=s.note,
        )
        n += 1
    return n


def my_upcoming(member, days: int = 7) -> list[RotaShift]:
    today = timezone.localdate()
    return list(
        RotaShift.objects.filter(
            staff_member=member, published=True, date__gte=today, date__lt=today + timedelta(days=days)
        ).order_by("date", "start_time")
    )


def send_shift_reminders(now=None) -> int:
    """Hourly: a push an hour before a published shift starts."""
    from apps.notifications import services as notifications

    now = now or timezone.now()
    n = 0
    qs = RotaShift.objects.filter(
        published=True, reminder_sent_at__isnull=True, date__in=[now.date(), (now + timedelta(days=1)).date()]
    ).select_related("restaurant", "staff_member__user")
    for s in qs:
        if not enabled(s.restaurant):
            continue
        starts = s.starts_at(restaurant_tz(s.restaurant))
        if not (now <= starts <= now + timedelta(minutes=75)):
            continue
        notifications.notify(
            s.restaurant,
            "shift.reminder",
            title=_("Your shift starts at %(time)s") % {"time": f"{s.start_time:%H:%M}"},
            body=s.label or "",
            data={"kind": "rota", "id": str(s.pk)},
            dedupe_key=f"shift.reminder:{s.pk}",
            users=[s.staff_member.user],
        )
        s.reminder_sent_at = now
        s.save(update_fields=["reminder_sent_at", "updated_at"])
        n += 1
    return n


# ── audit ─────────────────────────────────────────────────────────────────


def _audit(action, entry, user, description):
    _audit_plain(action, entry.restaurant, user, description, target_model="timeentry", target_id=str(entry.pk))


def _audit_plain(action, restaurant, user, description, target_model="", target_id=""):
    try:
        from apps.audit.services import log_action

        log_action(
            action,
            restaurant=restaurant,
            user=user if getattr(user, "is_authenticated", False) else None,
            description=description,
            target_model=target_model,
            target_id=target_id,
        )
    except Exception:  # pragma: no cover
        pass
