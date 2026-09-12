"""
Customer identity (phone / user), stats rollup from orders + reservations +
reviews, consent, segments, campaigns (batched through the notifications
module) and automations.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core import signing
from django.db.models import Avg, Count, Max, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.crm.models import Automation, AutomationSend, Campaign, CampaignDelivery, Customer, Segment
from apps.notifications import services as notifications
from apps.notifications.providers import normalize_phone

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
BATCH = 50

BUILTIN_SEGMENTS = [
    ("Everyone (opted in)", {"opt_in": True}, "All guests who agreed to hear from you."),
    ("Regulars", {"min_visits": 3, "opt_in": True}, "Three visits or more."),
    ("New guests", {"max_visits": 1, "last_visit_days_max": 14, "opt_in": True}, "First visit in the last two weeks."),
    ("Lapsed", {"last_visit_days_min": 45, "opt_in": True}, "No visit for 45 days."),
    ("Birthday this month", {"birthday_month": "current", "opt_in": True}, "Birthday in the current month."),
]


class CrmError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def enabled(restaurant) -> bool:
    return bool(getattr(restaurant, "crm_enabled", False))


# ── identity ──────────────────────────────────────────────────────────────


def identify(
    restaurant, *, user=None, phone: str = "", email: str = "", name: str = "", source: str = "", create=True
) -> Customer | None:
    """Find (or create) the customer record for a guest; merges a phone-only record when a user shows up with the same phone."""
    user = user if getattr(user, "is_authenticated", False) else None
    phone = normalize_phone(phone or (getattr(user, "phone_number", "") or ""))
    email = (email or (getattr(user, "email", "") or "")).strip().lower()
    customer = None
    if user is not None:
        customer = Customer.objects.filter(restaurant=restaurant, user=user).first()
    if customer is None and phone:
        customer = Customer.objects.filter(restaurant=restaurant, phone=phone).first()
        if customer is not None and user is not None and customer.user_id is None:
            if not Customer.objects.filter(restaurant=restaurant, user=user).exists():
                customer.user = user
                customer.save(update_fields=["user", "updated_at"])
    if customer is None and email and user is None:
        customer = Customer.objects.filter(restaurant=restaurant, email=email, user__isnull=True).first()
    if customer is None:
        if not create or not (user or phone or email):
            return None
        customer = Customer.objects.create(
            restaurant=restaurant,
            user=user,
            phone=phone,
            email=email,
            name=(name or (user.get_full_name() if user else "") or "")[:200],
            language=(getattr(user, "preferred_language", "") or "")[:2],
            source=source[:20],
            birthday=getattr(getattr(user, "profile", None), "date_of_birth", None),
        )
        if user is not None:
            profile = getattr(user, "profile", None)
            if profile is not None and getattr(profile, "marketing_opt_in", False):
                set_consent(customer, True, source="profile")
        return customer
    changed = []
    if name and not customer.name:
        customer.name = name[:200]
        changed.append("name")
    if email and not customer.email:
        customer.email = email
        changed.append("email")
    if phone and not customer.phone:
        customer.phone = phone
        changed.append("phone")
    if changed:
        customer.save(update_fields=changed + ["updated_at"])
    return customer


def set_consent(customer: Customer, opt_in: bool, *, source: str = "") -> Customer:
    now = timezone.now()
    if opt_in and not customer.marketing_opt_in:
        customer.marketing_opt_in = True
        customer.opt_in_at = now
        customer.opt_in_source = source[:30]
        customer.opt_out_at = None
        customer.save(update_fields=["marketing_opt_in", "opt_in_at", "opt_in_source", "opt_out_at", "updated_at"])
    elif not opt_in and customer.marketing_opt_in:
        customer.marketing_opt_in = False
        customer.opt_out_at = now
        customer.save(update_fields=["marketing_opt_in", "opt_out_at", "updated_at"])
    return customer


def sync_user_consent(user) -> int:
    """A user changed marketing consent in their profile: apply it to every restaurant record they have."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return 0
    n = 0
    for c in Customer.objects.filter(user=user):
        set_consent(c, bool(profile.marketing_opt_in), source="profile")
        if profile.date_of_birth and c.birthday != profile.date_of_birth:
            c.birthday = profile.date_of_birth
            c.save(update_fields=["birthday", "updated_at"])
        n += 1
    return n


# ── stats ─────────────────────────────────────────────────────────────────


def rebuild(customer: Customer) -> Customer:
    """Recompute counters from the ledgers (idempotent; used by hooks and the nightly job)."""
    from apps.orders.models import Order
    from apps.reservations.models import Reservation
    from apps.reviews.models import Review

    r = customer.restaurant
    who = Q(pk__in=[])
    if customer.user_id:
        who |= Q(customer_id=customer.user_id)
    if customer.phone:
        who |= Q(customer_phone__in=_phone_variants(customer.phone))
    orders = (
        Order.objects.filter(who, restaurant=r, status="completed")
        if (customer.user_id or customer.phone)
        else Order.objects.none()
    )
    agg = orders.aggregate(n=Count("id"), spend=Sum("total"), avg=Avg("total"), last=Max("completed_at"))
    res_q = Q(pk__in=[])
    if customer.user_id:
        res_q |= Q(customer_id=customer.user_id)
    if customer.phone:
        res_q |= Q(guest_phone__in=_phone_variants(customer.phone))
    reservations = (
        Reservation.objects.filter(res_q, restaurant=r, status__in=("seated", "completed"))
        if (customer.user_id or customer.phone)
        else Reservation.objects.none()
    )
    ragg = reservations.aggregate(n=Count("id"), last=Max("completed_at"))
    reviews = (
        Review.objects.filter(restaurant=r, user_id=customer.user_id) if customer.user_id else Review.objects.none()
    )
    review = reviews.order_by("-created_at").first()
    visits = (agg["n"] or 0) + (ragg["n"] or 0)
    last_visit = max([d for d in (agg["last"], ragg["last"]) if d], default=customer.last_visit_at)
    customer.orders_count = agg["n"] or 0
    customer.reservations_count = ragg["n"] or 0
    customer.reviews_count = reviews.count()
    customer.total_spend = (agg["spend"] or ZERO).quantize(Decimal("0.01"))
    customer.avg_ticket = (agg["avg"] or ZERO).quantize(Decimal("0.01"))
    customer.visits = visits
    customer.last_order_at = agg["last"]
    customer.last_visit_at = last_visit
    customer.last_rating = review.rating if review else customer.last_rating
    customer.save(
        update_fields=[
            "orders_count",
            "reservations_count",
            "reviews_count",
            "total_spend",
            "avg_ticket",
            "visits",
            "last_order_at",
            "last_visit_at",
            "last_rating",
            "updated_at",
        ]
    )
    return customer


def _phone_variants(phone: str) -> list[str]:
    out = {phone}
    if phone.startswith("+995"):
        out.add(phone[4:])
        out.add(phone[1:])
    return list(out)


def rebuild_all(restaurant=None) -> int:
    qs = Customer.objects.all() if restaurant is None else Customer.objects.filter(restaurant=restaurant)
    n = 0
    for c in qs.select_related("restaurant").iterator(chunk_size=200):
        rebuild(c)
        n += 1
    return n


def backfill(restaurant) -> int:
    """Create customer records from existing orders / reservations (module toggle, nightly safety net)."""
    from apps.orders.models import Order
    from apps.reservations.models import Reservation

    before = Customer.objects.filter(restaurant=restaurant).count()
    orders = (
        Order.objects.filter(restaurant=restaurant, status="completed")
        .exclude(customer__isnull=True, customer_phone="")
        .select_related("customer")
    )
    for o in orders.iterator(chunk_size=200):
        identify(
            restaurant,
            user=o.customer,
            phone=o.customer_phone,
            email=o.customer_email,
            name=o.customer_name,
            source=o.source,
        )
    reservations = Reservation.objects.filter(restaurant=restaurant, status__in=("seated", "completed")).select_related(
        "customer"
    )
    for r in reservations.iterator(chunk_size=200):
        identify(
            restaurant,
            user=r.customer,
            phone=r.guest_phone,
            email=r.guest_email,
            name=r.guest_name,
            source="reservation",
        )
    rebuild_all(restaurant)
    return Customer.objects.filter(restaurant=restaurant).count() - before


# ── hooks (called from other apps) ────────────────────────────────────────


def touch_from_order(order) -> Customer | None:
    if not enabled(order.restaurant):
        return None
    c = identify(
        order.restaurant,
        user=order.customer,
        phone=order.customer_phone,
        email=order.customer_email,
        name=order.customer_name,
        source=order.source,
    )
    if c is None:
        return None
    if getattr(order, "marketing_opt_in", False):
        set_consent(c, True, source="checkout")
    return rebuild(c)


def touch_from_reservation(reservation, *, consent=None) -> Customer | None:
    if not enabled(reservation.restaurant):
        return None
    c = identify(
        reservation.restaurant,
        user=reservation.customer,
        phone=reservation.guest_phone,
        email=reservation.guest_email,
        name=reservation.guest_name,
        source="reservation",
    )
    if c is None:
        return None
    if consent:
        set_consent(c, True, source="booking")
    return rebuild(c)


def touch_from_review(review) -> Customer | None:
    if not enabled(review.restaurant) or not review.user_id:
        return None
    c = identify(review.restaurant, user=review.user, source="review")
    return rebuild(c) if c else None


# ── segments ──────────────────────────────────────────────────────────────


def seed_segments(restaurant) -> int:
    n = 0
    for name, rules, description in BUILTIN_SEGMENTS:
        _, created = Segment.objects.get_or_create(
            restaurant=restaurant, name=name, defaults={"rules": rules, "description": description, "is_builtin": True}
        )
        n += int(created)
    for kind, _label in Automation.KIND_CHOICES:
        Automation.objects.get_or_create(restaurant=restaurant, kind=kind)
    return n


def segment_queryset(restaurant, rules: dict):
    now = timezone.now()
    qs = Customer.objects.filter(restaurant=restaurant)
    rules = rules or {}
    if rules.get("opt_in", True):
        qs = qs.filter(marketing_opt_in=True)
    if rules.get("min_visits") is not None:
        qs = qs.filter(visits__gte=int(rules["min_visits"]))
    if rules.get("max_visits") is not None:
        qs = qs.filter(visits__lte=int(rules["max_visits"]))
    if rules.get("min_spend") is not None:
        qs = qs.filter(total_spend__gte=Decimal(str(rules["min_spend"])))
    if rules.get("last_visit_days_max") is not None:
        qs = qs.filter(last_visit_at__gte=now - timedelta(days=int(rules["last_visit_days_max"])))
    if rules.get("last_visit_days_min") is not None:
        qs = qs.filter(last_visit_at__lt=now - timedelta(days=int(rules["last_visit_days_min"])))
    if rules.get("tags_any"):
        cond = Q()
        for tag in rules["tags_any"]:
            cond |= Q(tags__contains=[tag])
        qs = qs.filter(cond)
    if rules.get("birthday_month"):
        month = timezone.localdate().month if rules["birthday_month"] == "current" else int(rules["birthday_month"])
        qs = qs.filter(birthday__month=month)
    if rules.get("has_email"):
        qs = qs.exclude(email="")
    if rules.get("has_phone"):
        qs = qs.exclude(phone="")
    if rules.get("language"):
        qs = qs.filter(language=rules["language"])
    return qs


def segment_count(segment: Segment) -> int:
    return segment_queryset(segment.restaurant, segment.rules).count()


# ── messaging helpers ─────────────────────────────────────────────────────


def unsubscribe_token(customer: Customer) -> str:
    return signing.TimestampSigner(salt="crm-unsubscribe").sign(str(customer.pk))


def customer_from_token(token: str) -> Customer | None:
    try:
        pk = signing.TimestampSigner(salt="crm-unsubscribe").unsign(token, max_age=60 * 60 * 24 * 365)
    except signing.BadSignature:
        return None
    return Customer.objects.filter(pk=pk).first()


def unsubscribe_url(customer: Customer) -> str:
    base = getattr(settings, "PUBLIC_API_BASE_URL", "").rstrip("/")
    return f"{base}/api/v1/crm/unsubscribe/{unsubscribe_token(customer)}/"


def review_url(restaurant, customer: Customer | None = None) -> str:
    base = getattr(settings, "FRONTEND_BASE_URL", "https://aimenu.ge").rstrip("/")
    lang = (
        customer.language if customer and customer.language else getattr(restaurant, "default_language", "ka")
    ) or "ka"
    return f"{base}/{lang}/profile/reviews"


def render_for(customer: Customer, template: str, *, promotion=None, link: str = "") -> str:
    return notifications.render(
        template,
        name=customer.name.split(" ")[0] if customer.name else "",
        restaurant=customer.restaurant.name,
        code=promotion.code if promotion is not None else "",
        link=link or review_url(customer.restaurant, customer),
        unsubscribe=unsubscribe_url(customer),
    )


def _send(customer: Customer, channel: str, body: str, *, subject: str, kind: str, ref, by=None):
    if channel == "sms":
        if not customer.can_sms:
            return None
        return notifications.send_message(
            customer.restaurant, "sms", customer.phone, body, kind=kind, ref=ref, by=by, force=True
        )
    if not customer.can_email:
        return None
    text = (
        body if "{unsubscribe}" in body or "unsubscribe" in body.lower() else f"{body}\n\n{unsubscribe_url(customer)}"
    )
    return notifications.send_message(
        customer.restaurant, "email", customer.email, text, subject=subject, kind=kind, ref=ref, by=by, force=True
    )


# ── campaigns ─────────────────────────────────────────────────────────────


def audience(campaign: Campaign):
    qs = segment_queryset(campaign.restaurant, campaign.segment.rules)
    return qs.exclude(phone="") if campaign.channel == "sms" else qs.exclude(email="")


def preview(campaign: Campaign, customer: Customer | None = None) -> str:
    sample = (
        customer
        or audience(campaign).first()
        or Customer(restaurant=campaign.restaurant, name="Nino", phone="+995500000000")
    )
    return render_for(sample, campaign.body, promotion=campaign.promotion)


def send_test(campaign: Campaign, to: str, *, by=None):
    """Send the rendered campaign to one address / phone (no consent needed: it is the manager's own)."""
    sample = Customer(
        restaurant=campaign.restaurant,
        name=getattr(by, "first_name", "") or "Test",
        phone=to,
        email=to,
        marketing_opt_in=True,
    )
    body = render_for(sample, campaign.body, promotion=campaign.promotion)
    return notifications.send_message(
        campaign.restaurant,
        campaign.channel,
        to,
        body,
        subject=campaign.subject or campaign.name,
        kind="campaign",
        ref=campaign,
        by=by,
        force=True,
    )


def start_campaign(campaign: Campaign, *, by=None, when=None) -> Campaign:
    if campaign.status not in ("draft", "scheduled"):
        raise CrmError("bad_state", _("This campaign was already sent."))
    if not campaign.body.strip():
        raise CrmError("empty", _("Write the message first."))
    campaign.audience_count = audience(campaign).count()
    if when and when > timezone.now():
        campaign.scheduled_at = when
        campaign.status = "scheduled"
        campaign.save(update_fields=["audience_count", "scheduled_at", "status", "updated_at"])
        return campaign
    campaign.status = "sending"
    campaign.started_at = timezone.now()
    campaign.save(update_fields=["audience_count", "status", "started_at", "updated_at"])
    from apps.core.enqueue import enqueue
    from apps.crm import tasks

    enqueue(tasks.send_campaign_batch, str(campaign.pk))
    return campaign


def send_batch(campaign: Campaign, *, size: int = BATCH) -> int:
    """Deliver up to ``size`` recipients; returns how many were handled (0 = finished)."""
    if campaign.status != "sending":
        return 0
    done_ids = set(CampaignDelivery.objects.filter(campaign=campaign).values_list("customer_id", flat=True))
    rows = list(audience(campaign).exclude(pk__in=done_ids)[:size])
    if not rows:
        campaign.status = "sent"
        campaign.finished_at = timezone.now()
        campaign.save(update_fields=["status", "finished_at", "updated_at"])
        notifications.notify(
            campaign.restaurant,
            "campaign.finished",
            title=_("Campaign '%(name)s' sent") % {"name": campaign.name},
            body=_("%(sent)d sent, %(failed)d failed, %(skipped)d skipped")
            % {"sent": campaign.sent_count, "failed": campaign.failed_count, "skipped": campaign.skipped_count},
            data={"kind": "campaign", "id": str(campaign.pk)},
            dedupe_key=f"campaign.finished:{campaign.pk}",
        )
        return 0
    sent = failed = skipped = 0
    for c in rows:
        body = render_for(c, campaign.body, promotion=campaign.promotion)
        msg = _send(c, campaign.channel, body, subject=campaign.subject or campaign.name, kind="campaign", ref=campaign)
        status = "skipped" if msg is None else ("queued" if msg.status == "queued" else msg.status)
        CampaignDelivery.objects.create(campaign=campaign, customer=c, message=msg, status=status)
        if msg is None or msg.status == "skipped":
            skipped += 1
        elif msg.status == "failed":
            failed += 1
        else:
            sent += 1
    Campaign.objects.filter(pk=campaign.pk).update(
        sent_count=campaign.sent_count + sent,
        failed_count=campaign.failed_count + failed,
        skipped_count=campaign.skipped_count + skipped,
    )
    campaign.refresh_from_db(fields=["sent_count", "failed_count", "skipped_count"])
    return len(rows)


def cancel_campaign(campaign: Campaign) -> Campaign:
    if campaign.status in ("draft", "scheduled", "sending"):
        campaign.status = "cancelled"
        campaign.save(update_fields=["status", "updated_at"])
    return campaign


def run_scheduled(now=None) -> int:
    now = now or timezone.now()
    n = 0
    for c in Campaign.objects.filter(status="scheduled", scheduled_at__lte=now).select_related("restaurant", "segment"):
        if not enabled(c.restaurant):
            continue
        start_campaign(c)
        n += 1
    return n


# ── automations ───────────────────────────────────────────────────────────


def _auto_send(automation: Automation, customer: Customer, key: str, *, link: str = "") -> bool:
    if AutomationSend.objects.filter(automation=automation, customer=customer, key=key).exists():
        return False
    body = render_for(customer, automation.template(), promotion=automation.promotion, link=link)
    msg = _send(
        customer, automation.channel, body, subject=automation.get_kind_display(), kind="automation", ref=automation
    )
    if msg is None:
        return False
    AutomationSend.objects.create(automation=automation, customer=customer, key=key, message=msg)
    Automation.objects.filter(pk=automation.pk).update(sent_count=automation.sent_count + 1)
    return True


def run_birthdays(now=None) -> int:
    now = now or timezone.now()
    today = timezone.localdate(now)
    n = 0
    for a in Automation.objects.filter(kind="birthday", enabled=True).select_related("restaurant", "promotion"):
        if not enabled(a.restaurant):
            continue
        for c in Customer.objects.filter(
            restaurant=a.restaurant, marketing_opt_in=True, birthday__month=today.month, birthday__day=today.day
        ):
            n += int(_auto_send(a, c, key=str(today.year)))
    return n


def run_review_prompts(now=None) -> int:
    """Orders completed between delay_hours and 48 h ago whose customer has not been asked yet."""
    from apps.orders.models import Order

    now = now or timezone.now()
    n = 0
    for a in Automation.objects.filter(kind="review_prompt", enabled=True).select_related("restaurant"):
        if not enabled(a.restaurant):
            continue
        window_end = now - timedelta(hours=int(a.delay_hours or 0))
        window_start = now - timedelta(hours=48)
        orders = Order.objects.filter(
            restaurant=a.restaurant, status="completed", completed_at__gte=window_start, completed_at__lte=window_end
        ).exclude(customer__isnull=True, customer_phone="")
        for o in orders.select_related("customer"):
            c = identify(
                a.restaurant,
                user=o.customer,
                phone=o.customer_phone,
                email=o.customer_email,
                name=o.customer_name,
                source=o.source,
                create=False,
            )
            if c is None or not c.marketing_opt_in:
                continue
            n += int(_auto_send(a, c, key=str(o.pk), link=review_url(a.restaurant, c)))
    return n


def run_winbacks(now=None) -> int:
    now = now or timezone.now()
    n = 0
    for a in Automation.objects.filter(kind="winback", enabled=True).select_related("restaurant", "promotion"):
        if not enabled(a.restaurant):
            continue
        cutoff = now - timedelta(days=int(a.lapsed_days or 45))
        floor = now - timedelta(days=int(a.lapsed_days or 45) + 30)  # do not chase guests gone for months
        key = timezone.localdate(now).strftime("%Y-%m")
        for c in Customer.objects.filter(
            restaurant=a.restaurant, marketing_opt_in=True, last_visit_at__lt=cutoff, last_visit_at__gte=floor
        ):
            n += int(_auto_send(a, c, key=key))
    return n


# ── reporting ─────────────────────────────────────────────────────────────


def summary(restaurant) -> dict:
    qs = Customer.objects.filter(restaurant=restaurant)
    month = timezone.now() - timedelta(days=30)
    return {
        "customers": qs.count(),
        "opted_in": qs.filter(marketing_opt_in=True).count(),
        "new_30d": qs.filter(first_seen_at__gte=month).count(),
        "returning_30d": qs.filter(last_visit_at__gte=month, visits__gte=2).count(),
        "campaigns_30d": Campaign.objects.filter(restaurant=restaurant, status="sent", finished_at__gte=month).count(),
        "messages_30d": sum(
            Campaign.objects.filter(restaurant=restaurant, finished_at__gte=month).values_list("sent_count", flat=True)
        ),
    }


def crm_report(restaurant, start, end) -> dict:
    qs = Customer.objects.filter(restaurant=restaurant)
    new = qs.filter(first_seen_at__gte=start, first_seen_at__lt=end).count()
    active = qs.filter(last_visit_at__gte=start, last_visit_at__lt=end)
    returning = active.filter(visits__gte=2).count()
    top = list(
        qs.order_by("-total_spend")[:15].values("name", "phone", "visits", "total_spend", "avg_ticket", "last_visit_at")
    )
    campaigns = list(
        Campaign.objects.filter(restaurant=restaurant, started_at__gte=start, started_at__lt=end).values(
            "name", "channel", "audience_count", "sent_count", "failed_count", "skipped_count", "status"
        )
    )
    return {
        "new": new,
        "active": active.count(),
        "returning": returning,
        "top": top,
        "campaigns": campaigns,
        "opted_in": qs.filter(marketing_opt_in=True).count(),
    }
