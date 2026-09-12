from celery import shared_task

from apps.printing import services


@shared_task(name="printing.requeue_stale_jobs", ignore_result=True)
def requeue_stale_jobs() -> int:
    """Every minute: jobs a bridge claimed but never acknowledged go back to the queue."""
    return services.requeue_stale()
