import logging

from celery import shared_task

from apps.notifications import services
from apps.notifications.models import OutboundMessage

logger = logging.getLogger(__name__)


@shared_task(name="notifications.push", bind=True, max_retries=3, ignore_result=True)
def push(self, notification_ids):
    try:
        services.deliver_push(notification_ids)
    except Exception as exc:  # noqa: BLE001
        logger.exception("push delivery failed")
        if self.request.retries < self.max_retries and not _eager(self):
            raise self.retry(countdown=30 * (2**self.request.retries), exc=exc)


@shared_task(name="notifications.deliver", bind=True, max_retries=4, ignore_result=True, acks_late=True)
def deliver(self, message_id):
    msg = OutboundMessage.objects.select_related("restaurant").filter(pk=message_id).first()
    if msg is None:
        return
    try:
        services.deliver_message(msg)
    except services.RetryableDelivery as exc:
        if self.request.retries < self.max_retries and not _eager(self):
            raise self.retry(countdown=60 * (2**self.request.retries), exc=exc)
        logger.error("Giving up on message %s: %s", message_id, exc)


@shared_task(name="notifications.reservation_reminders", ignore_result=True)
def reservation_reminders():
    return services.send_due_reminders()


@shared_task(name="notifications.prune", ignore_result=True)
def prune():
    return services.prune()


def _eager(task) -> bool:
    return bool(getattr(task.app.conf, "task_always_eager", False))
