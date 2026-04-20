"""
Post-save signal on Order → award loyalty punches for the restaurant's
active programs. Runs only on the completed-transition so cancellations
and in-flight status changes don't double-count.

Customer resolution order:
    1. Authenticated order (`order.customer`) → use that User.
    2. `order.customer_phone` matches a registered User.phone_number → attach
       to that User.
    3. `order.customer_phone` present but no user match → anonymous
       phone-keyed counter. Will merge into a user later if that phone
       registers.
    4. Neither customer nor phone → skip (walk-in, no loyalty).
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import F
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.orders.models import Order

logger = logging.getLogger(__name__)


def _resolve_target(order: Order):
    """Return (user, phone) tuple. At least one should be truthy for us to
    credit anything."""
    from apps.accounts.models import User

    if order.customer_id:
        return order.customer, ""

    phone = (order.customer_phone or "").strip()
    if not phone:
        return None, ""

    user = User.objects.filter(phone_number=phone).first()
    if user:
        return user, ""
    return None, phone


@receiver(post_save, sender=Order)
def award_punches(sender, instance: Order, created, update_fields=None, **kwargs):
    if instance.status != "completed":
        return
    # Avoid running on unrelated saves (e.g. customer_notes tweak)
    if update_fields and "status" not in update_fields:
        return

    # Lazy imports to avoid circular load at startup
    from .models import LoyaltyCounter, LoyaltyProgram

    now = timezone.now()
    programs = (
        LoyaltyProgram.objects.filter(restaurant_id=instance.restaurant_id, is_active=True)
        .select_related("trigger_item")
    )
    active_programs = [p for p in programs if p.is_live(now)]
    if not active_programs:
        return

    user, phone = _resolve_target(instance)
    if user is None and not phone:
        return

    trigger_ids = {p.trigger_item_id: p for p in active_programs}
    # Count matching items per trigger in ONE query
    item_counts: dict = {}
    for oi in instance.items.all():
        if oi.menu_item_id in trigger_ids:
            item_counts[oi.menu_item_id] = item_counts.get(oi.menu_item_id, 0) + (oi.quantity or 0)

    if not item_counts:
        return

    with transaction.atomic():
        for trigger_id, qty in item_counts.items():
            program = trigger_ids[trigger_id]
            lookup = {"program": program}
            if user is not None:
                lookup["user"] = user
            else:
                lookup["phone_number"] = phone
            counter, _ = LoyaltyCounter.objects.get_or_create(
                **lookup,
                defaults={"punches": 0},
            )
            LoyaltyCounter.objects.filter(pk=counter.pk).update(
                punches=F("punches") + qty,
                last_earned_at=now,
                updated_at=now,
            )
    logger.info(
        "Loyalty: credited %s punches to %s across %d program(s) for order %s",
        sum(item_counts.values()),
        user.id if user else phone,
        len(item_counts),
        instance.id,
    )
