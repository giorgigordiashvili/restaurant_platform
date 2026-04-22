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
from django.db import transaction

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

    # Fetch pending rows without row-level locks (read-only snapshot is
    # fine — _attempt_settlement takes its own transaction around the
    # single-row update), then process one at a time. Previously we used
    # .select_for_update().iterator() but that requires the caller to be
    # in a transaction — running it at the top level of the Celery task
    # raised TransactionManagementError and crash-looped the worker.
    pending_ids = list(
        FlittTransaction.objects.filter(
            status=FlittTransaction.STATUS_APPROVED,
            settlement_status=FlittTransaction.SETTLEMENT_PENDING,
        ).values_list("id", flat=True)
    )

    tried = 0
    succeeded = 0
    gave_up = 0
    for txn_id in pending_ids:
        # Per-row transaction + select_for_update(skip_locked=True) so
        # two workers running this task simultaneously don't double-fire
        # the same settlement call.
        with transaction.atomic():
            try:
                txn = FlittTransaction.objects.select_for_update(skip_locked=True).get(id=txn_id)
            except FlittTransaction.DoesNotExist:
                continue
            if txn.settlement_status != FlittTransaction.SETTLEMENT_PENDING:
                # Something else picked it up already.
                continue
            tried += 1
            _attempt_settlement(txn)
            txn.refresh_from_db(fields=["settlement_status"])
            if txn.settlement_status == FlittTransaction.SETTLEMENT_SETTLED:
                succeeded += 1
            # Cap retries at the configured ceiling — if we're still failing
            # a day later something's structurally off; platform admin should
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
