"""
``notify()`` for staff, ``send_message()`` for guests. Both only write rows
and enqueue; the tasks talk to Expo / SMS / email.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.enqueue import enqueue
from apps.notifications import events, providers
from apps.notifications.models import (
    Device,
    Notification,
    OutboundMessage,
    RestaurantNotificationSettings,
    StaffNotificationPrefs,
)

logger = logging.getLogger(__name__)

DEDUPE_WINDOW = timedelta(hours=1)


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "notifications_enabled", True))


def settings_for(restaurant) -> RestaurantNotificationSettings:
    row, _ = RestaurantNotificationSettings.objects.get_or_create(restaurant=restaurant)
    return row


def prefs_for(restaurant, user) -> StaffNotificationPrefs:
    row, _ = StaffNotificationPrefs.objects.get_or_create(restaurant=restaurant, user=user)
    return row


# ── recipients ────────────────────────────────────────────────────────────


def recipients(restaurant, event: events.Event, *, users=None) -> list:
    """Owner + active staff whose effective permissions cover the event, minus mutes."""
    from apps.staff.models import StaffMember

    candidates = {}
    if users is not None:
        for u in users:
            candidates[u.pk] = u
    else:
        if restaurant.owner_id:
            candidates[restaurant.owner_id] = restaurant.owner
        members = StaffMember.objects.filter(restaurant=restaurant, is_active=True).select_related("user", "role")
        for m in members:
            if m.user_id in candidates:
                continue
            allowed = m.get_effective_permissions().get(event.resource, [])
            if event.action in allowed or "*" in allowed:
                candidates[m.user_id] = m.user
    if not candidates:
        return []
    muted = {
        p.user_id
        for p in StaffNotificationPrefs.objects.filter(restaurant=restaurant, user_id__in=list(candidates))
        if event.code in (p.muted_events or [])
    }
    return [u for pk, u in candidates.items() if pk not in muted]


# ── staff notifications ───────────────────────────────────────────────────


def notify(
    restaurant,
    event_code: str,
    *,
    title: str,
    body: str = "",
    data=None,
    url: str = "",
    dedupe_key: str = "",
    users=None,
) -> list[Notification]:
    """Create one Notification per recipient and queue the push. Never raises past the hook wrappers."""
    if not enabled(restaurant):
        return []
    event = events.get(event_code)
    people = recipients(restaurant, event, users=users)
    if not people:
        return []
    if dedupe_key:
        recent = set(
            Notification.objects.filter(
                restaurant=restaurant,
                dedupe_key=dedupe_key,
                created_at__gte=timezone.now() - DEDUPE_WINDOW,
                user_id__in=[u.pk for u in people],
            ).values_list("user_id", flat=True)
        )
        people = [u for u in people if u.pk not in recent]
        if not people:
            return []
    rows = Notification.objects.bulk_create(
        [
            Notification(
                restaurant=restaurant,
                user=u,
                event=event.code,
                title=title[:200],
                body=body or "",
                data={**(data or {}), "event": event.code, "sound": event.sound},
                url=url[:300],
                dedupe_key=dedupe_key[:150],
            )
            for u in people
        ]
    )
    from apps.notifications import tasks

    enqueue(tasks.push, [str(r.pk) for r in rows])
    return rows


def _in_quiet_hours(prefs: StaffNotificationPrefs, now) -> bool:
    if not prefs.quiet_from or not prefs.quiet_to:
        return False
    t = timezone.localtime(now).time()
    if prefs.quiet_from <= prefs.quiet_to:
        return prefs.quiet_from <= t < prefs.quiet_to
    return t >= prefs.quiet_from or t < prefs.quiet_to  # overnight window


def deliver_push(notification_ids: list[str], *, session=None) -> dict:
    """Send the queued notifications to every active device of each recipient; email when preferred."""
    rows = list(
        Notification.objects.filter(pk__in=notification_ids, pushed_at__isnull=True).select_related(
            "restaurant", "user"
        )
    )
    if not rows:
        return {"pushed": 0, "emailed": 0}
    now = timezone.now()
    prefs = {
        (p.restaurant_id, p.user_id): p
        for p in StaffNotificationPrefs.objects.filter(
            restaurant_id__in={r.restaurant_id for r in rows}, user_id__in={r.user_id for r in rows}
        )
    }
    devices = {}
    for d in Device.objects.filter(user_id__in={r.user_id for r in rows}, is_active=True, kind="expo"):
        devices.setdefault(d.user_id, []).append(d)
    messages, owners = [], []
    emailed = 0
    for n in rows:
        p = prefs.get((n.restaurant_id, n.user_id))
        want_push = p is None or (p.push and not _in_quiet_hours(p, now))
        if want_push:
            for d in devices.get(n.user_id, []):
                messages.append(
                    {
                        "to": d.token,
                        "title": n.title,
                        "body": n.body[:180],
                        "data": {**n.data, "notification_id": str(n.pk), "url": n.url},
                        "sound": "default" if n.data.get("sound", True) else None,
                        "channelId": "orders" if n.event.startswith("order") else "default",
                    }
                )
                owners.append((n, d))
        if p is not None and p.email and n.user.email:
            result = providers.send_email(n.user.email, f"[{n.restaurant.name}] {n.title}", n.body or n.title)
            if result.ok:
                n.emailed_at = now
                emailed += 1
    pushed = 0
    if messages:
        try:
            tickets = providers.send_expo_push(messages, session=session)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Expo push failed: %s", exc)
            for n, _d in owners:
                n.push_error = str(exc)[:200]
            tickets = []
        for (n, d), ticket in zip(owners, tickets):
            if ticket.get("status") == "ok":
                n.pushed_at = now
                n.push_error = ""
                pushed += 1
            else:
                err = str((ticket.get("details") or {}).get("error") or ticket.get("message") or "error")
                n.push_error = err[:200]
                if err == "DeviceNotRegistered":
                    Device.objects.filter(pk=d.pk).update(is_active=False, last_error=err)
    for n in rows:
        if n.pushed_at is None and not n.push_error:
            n.pushed_at = now  # nothing to push to: mark handled so the task does not loop
            n.push_error = "no device" if not devices.get(n.user_id) else "push off"
        n.save(update_fields=["pushed_at", "push_error", "emailed_at", "updated_at"])
    return {"pushed": pushed, "emailed": emailed}


def register_device(user, restaurant, *, token: str, kind: str = "expo", platform: str = "", app_version: str = ""):
    device, _ = Device.objects.update_or_create(
        token=token,
        defaults={
            "user": user,
            "restaurant": restaurant,
            "kind": kind,
            "platform": platform[:20],
            "app_version": app_version[:40],
            "is_active": True,
            "last_error": "",
        },
    )
    return device


def unread_count(restaurant, user) -> int:
    return Notification.objects.filter(restaurant=restaurant, user=user, read_at__isnull=True).count()


def mark_read(restaurant, user, ids=None) -> int:
    qs = Notification.objects.filter(restaurant=restaurant, user=user, read_at__isnull=True)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    return qs.update(read_at=timezone.now())


# ── guest messages ────────────────────────────────────────────────────────


def render(template: str, **ctx) -> str:
    class _Safe(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    try:
        return template.format_map(_Safe(**{k: ("" if v is None else v) for k, v in ctx.items()}))
    except Exception:  # noqa: BLE001
        return template


def send_message(
    restaurant,
    channel: str,
    to: str,
    body: str,
    *,
    subject: str = "",
    kind: str = "other",
    ref=None,
    by=None,
    force: bool = False,
) -> OutboundMessage | None:
    """Queue one guest message. ``force`` bypasses the restaurant's guest_sms / guest_email switches (tests, campaigns)."""
    cfg = settings_for(restaurant)
    if not to:
        return None
    if channel == "sms":
        to = providers.normalize_phone(to)
        allowed = force or cfg.guest_sms
    else:
        allowed = force or cfg.guest_email
    msg = OutboundMessage.objects.create(
        restaurant=restaurant,
        channel=channel,
        to=to[:254],
        subject=subject[:200],
        body=body,
        kind=kind,
        ref_model=type(ref).__name__.lower() if ref is not None else "",
        ref_id=str(getattr(ref, "pk", "") or "") if ref is not None else "",
        status="queued" if allowed else "skipped",
        error="" if allowed else f"guest {channel} switched off",
        created_by=by if getattr(by, "is_authenticated", False) else None,
    )
    if allowed:
        from apps.notifications import tasks

        enqueue(tasks.deliver, str(msg.pk))
    return msg


def deliver_message(msg: OutboundMessage, *, session=None) -> OutboundMessage:
    if msg.status == "sent":
        return msg
    cfg = settings_for(msg.restaurant)
    sender = cfg.sender_name or msg.restaurant.name
    if msg.channel == "sms":
        result = providers.send_sms(msg.to, msg.body, from_name=sender, session=session)
    else:
        result = providers.send_email(msg.to, msg.subject or sender, msg.body, from_name=sender)
    msg.attempts += 1
    msg.provider = result.provider
    msg.provider_id = result.provider_id
    if result.ok:
        msg.status = "sent"
        msg.sent_at = timezone.now()
        msg.error = ""
    elif result.skipped:
        msg.status = "skipped"
        msg.error = result.error
    else:
        msg.status = "failed"
        msg.error = result.error
    msg.save(update_fields=["attempts", "provider", "provider_id", "status", "sent_at", "error", "updated_at"])
    if not result.ok and not result.skipped and result.retryable:
        raise RetryableDelivery(result.error)
    return msg


class RetryableDelivery(Exception):
    pass


# ── reservations ──────────────────────────────────────────────────────────


def _reservation_ctx(reservation) -> dict:
    return {
        "name": reservation.guest_name,
        "restaurant": reservation.restaurant.name,
        "date": reservation.reservation_date.strftime("%d.%m"),
        "time": reservation.reservation_time.strftime("%H:%M"),
        "guests": reservation.party_size,
        "code": reservation.confirmation_code,
    }


def _guest_language(reservation) -> str:
    user = getattr(reservation, "customer", None)
    lang = getattr(user, "preferred_language", None) if user else None
    return lang or getattr(reservation.restaurant, "default_language", "ka") or "ka"


def message_guest(restaurant, *, kind: str, reservation=None, phone: str = "", email: str = "", language="", ctx=None):
    """Send the restaurant's template for ``kind`` by SMS (phone) and/or email, whichever the guest gave."""
    cfg = settings_for(restaurant)
    language = language or "ka"
    text = render(cfg.template(kind, language), **(ctx or {}))
    out = []
    subject = {"reservation_confirmation": "Reservation confirmed", "reservation_reminder": "Reservation reminder"}.get(
        kind, restaurant.name
    )
    if phone and cfg.guest_sms:
        out.append(send_message(restaurant, "sms", phone, text, kind=kind, ref=reservation))
    if email and cfg.guest_email:
        out.append(send_message(restaurant, "email", email, text, subject=subject, kind=kind, ref=reservation))
    return [m for m in out if m is not None]


def reservation_confirmed(reservation) -> None:
    if not enabled(reservation.restaurant):
        return
    message_guest(
        reservation.restaurant,
        kind="reservation_confirmation",
        reservation=reservation,
        phone=reservation.guest_phone,
        email=reservation.guest_email,
        language=_guest_language(reservation),
        ctx=_reservation_ctx(reservation),
    )


def send_due_reminders(now=None) -> int:
    """Hourly: remind guests of confirmed reservations starting within the restaurant's reminder window."""
    from datetime import datetime

    from apps.reservations.models import Reservation, ReservationSettings

    now = now or timezone.now()
    sent = 0
    settings_rows = ReservationSettings.objects.filter(send_reminder=True).select_related("restaurant")
    for cfg in settings_rows:
        restaurant = cfg.restaurant
        if not enabled(restaurant) or not getattr(restaurant, "accepts_reservations", True):
            continue
        tz = timezone.get_current_timezone()
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(getattr(restaurant, "timezone", "") or "Asia/Tbilisi")
        except Exception:  # noqa: BLE001
            pass
        horizon = now + timedelta(hours=int(cfg.reminder_hours_before or 24))
        candidates = Reservation.objects.filter(
            restaurant=restaurant,
            status="confirmed",
            reminder_sent=False,
            reservation_date__gte=timezone.localdate(now),
            reservation_date__lte=horizon.date(),
        )
        for r in candidates:
            starts = datetime.combine(r.reservation_date, r.reservation_time, tzinfo=tz)
            if starts <= now or starts > horizon:
                continue
            with transaction.atomic():
                message_guest(
                    restaurant,
                    kind="reservation_reminder",
                    reservation=r,
                    phone=r.guest_phone,
                    email=r.guest_email,
                    language=_guest_language(r),
                    ctx=_reservation_ctx(r),
                )
                Reservation.objects.filter(pk=r.pk).update(reminder_sent=True, reminder_sent_at=now)
            sent += 1
    return sent


def prune(days: int = 90) -> int:
    cutoff = timezone.now() - timedelta(days=days)
    n, _ = Notification.objects.filter(created_at__lt=cutoff).delete()
    return n
