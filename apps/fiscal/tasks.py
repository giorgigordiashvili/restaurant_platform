from django.db import transaction
from django.utils import timezone

from celery import shared_task

from apps.fiscal import services
from apps.fiscal.models import FiscalDocument


@shared_task(name="fiscal.issue_document", bind=True, ignore_result=True, max_retries=6, acks_late=True)
def issue_document(self, document_id):
    doc = FiscalDocument.objects.filter(pk=document_id).first()
    if doc is None or doc.status in services.TERMINAL:
        return
    doc = services.issue(doc)
    eager = bool(getattr(self.app.conf, "task_always_eager", False))
    if (
        doc.status == "failed"
        and doc.next_retry_at is not None
        and not eager
        and self.request.retries < self.max_retries
    ):
        raise self.retry(countdown=services.backoff(doc.attempts))


@shared_task(name="fiscal.retry_failed", ignore_result=True)
def retry_failed() -> int:
    """Beat, every 10 minutes: failed documents whose retry time has come."""
    n = 0
    now = timezone.now()
    ids = list(
        FiscalDocument.objects.filter(status="failed", next_retry_at__lte=now, attempts__lt=20).values_list(
            "pk", flat=True
        )[:200]
    )
    for pk in ids:
        with transaction.atomic():
            doc = FiscalDocument.objects.select_for_update(skip_locked=True).filter(pk=pk, status="failed").first()
            if doc is None:
                continue
            doc.status = "queued"
            doc.save(update_fields=["status", "updated_at"])
        issue_document.apply_async(args=[str(pk)])
        n += 1
    return n
