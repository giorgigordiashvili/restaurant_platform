"""``enqueue(task, *args)``: run a Celery task after the current transaction commits."""

from __future__ import annotations

from django.db import transaction


def enqueue(task, *args, queue: str | None = None, countdown: int | None = None) -> None:
    options = {}
    if queue:
        options["queue"] = queue
    if countdown:
        options["countdown"] = countdown
    transaction.on_commit(lambda: task.apply_async(args=args, **options))
