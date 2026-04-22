"""
Celery tasks for the payments app.

Currently one job: retry failed Flitt settlement calls. When a Flitt
checkout approves but the follow-up ``/api/settlement`` call fails
(network flake, 5xx, decline), the webhook handler marks the
``FlittTransaction.settlement_status`` as ``pending`` and this task
picks up every pending row on an hourly schedule.
"""

from __future__ import annotations

import logging

from celery import shared_task

from apps.payments.models import FlittTransaction

logger = logging.getLogger(__name__)

SETTLEMENT_MAX_RETRIES = 24  # ~1 day of hourly retries before giving up.


@shared_task(name="payments.retry_failed_flitt_settlements", ignore_result=True)
def retry_failed_flitt_settlements() -> dict[str, int]:
    """
    Re-attempt any Flitt transactions whose settlement is still pending.

    Returns a small summary dict for log readability.
    """
    from apps.payments.flitt.views import _attempt_settlement

    qs = FlittTransaction.objects.filter(
        status=FlittTransaction.STATUS_APPROVED,
        settlement_status=FlittTransaction.SETTLEMENT_PENDING,
    )
    tried = 0
    succeeded = 0
    gave_up = 0
    for txn in qs.select_for_update(skip_locked=True).iterator():
        tried += 1
        _attempt_settlement(txn)
        txn.refresh_from_db(fields=["settlement_status"])
        if txn.settlement_status == FlittTransaction.SETTLEMENT_SETTLED:
            succeeded += 1
        # Cap retries at the configured ceiling — if we're still failing a
        # day later, something's wrong structurally; platform admin should
        # inspect the row in Django /admin/ and decide.
        elif tried >= SETTLEMENT_MAX_RETRIES:
            gave_up += 1
            FlittTransaction.objects.filter(id=txn.id).update(settlement_status=FlittTransaction.SETTLEMENT_ERROR)

    if tried:
        logger.info(
            "Flitt settlement retry pass: tried=%s succeeded=%s gave_up=%s",
            tried,
            succeeded,
            gave_up,
        )
    return {"tried": tried, "succeeded": succeeded, "gave_up": gave_up}
