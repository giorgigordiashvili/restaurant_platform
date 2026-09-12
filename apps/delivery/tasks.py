import logging

from django.utils import timezone

from celery import shared_task

from apps.delivery import services
from apps.delivery.glovo.client import GlovoClientError, build_client
from apps.delivery.models import PlatformMenuSync, RestaurantDeliveryPlatform
from apps.orders.models import Order

logger = logging.getLogger(__name__)


@shared_task(name="delivery.sync_status", bind=True, max_retries=5, ignore_result=True, acks_late=True)
def sync_status(self, order_id, platform_status):
    order = Order.objects.select_related("restaurant").filter(pk=order_id).first()
    if order is None:
        return
    try:
        services.push_status(order, platform_status)
    except GlovoClientError as exc:
        if exc.retryable and self.request.retries < self.max_retries and not _eager(self):
            raise self.retry(countdown=10 * (2**self.request.retries))
        logger.error("Giving up pushing %s for order %s: %s", platform_status, order_id, exc)


@shared_task(name="delivery.push_menu", bind=True, max_retries=3, ignore_result=True)
def push_menu(self, link_id, sync_id):
    link = RestaurantDeliveryPlatform.objects.select_related("restaurant").filter(pk=link_id).first()
    sync = PlatformMenuSync.objects.filter(pk=sync_id).first()
    if link is None or sync is None:
        return
    sync.status = "sent"
    sync.started_at = timezone.now()
    url = services.menu_feed_url(link)
    sync.request = {"menuUrl": url}
    sync.save(update_fields=["status", "started_at", "request", "updated_at"])
    try:
        from apps.delivery.glovo.menu import build_menu

        sync.product_count = len(build_menu(link)["products"])
        response = build_client(link).upload_menu(url)
    except Exception as exc:  # noqa: BLE001 - recorded on the sync row
        sync.status = "failed"
        sync.error = str(exc)[:1000]
        sync.finished_at = timezone.now()
        sync.save(update_fields=["status", "error", "finished_at", "product_count", "updated_at"])
        _stamp(link, "failed")
        return
    sync.transaction_id = str(response.get("transactionId") or response.get("transaction_id") or "")
    sync.response = response
    sync.status = "processing" if sync.transaction_id else "success"
    sync.save(update_fields=["transaction_id", "response", "status", "product_count", "updated_at"])
    if sync.transaction_id:
        poll_menu_sync.apply_async(args=[str(sync.pk), 0], countdown=30 if not _eager(self) else 0)
    else:
        sync.finished_at = timezone.now()
        sync.save(update_fields=["finished_at", "updated_at"])
        _stamp(link, "success")


@shared_task(name="delivery.poll_menu_sync", bind=True, ignore_result=True)
def poll_menu_sync(self, sync_id, attempt=0):
    sync = PlatformMenuSync.objects.select_related("link__restaurant").filter(pk=sync_id).first()
    if sync is None or sync.status not in ("processing", "sent"):
        return
    try:
        status = build_client(sync.link).menu_upload_status(sync.transaction_id)
    except Exception as exc:  # noqa: BLE001
        sync.error = str(exc)[:1000]
        sync.save(update_fields=["error", "updated_at"])
        status = {}
    state = str(status.get("status", "")).upper()
    if state == "SUCCESS":
        sync.status = "success"
        sync.finished_at = timezone.now()
        sync.response = status
        sync.save(update_fields=["status", "finished_at", "response", "updated_at"])
        _stamp(sync.link, "success")
    elif state == "FAILED":
        sync.status = "failed"
        sync.error = str(status.get("details") or status)[:1000]
        sync.finished_at = timezone.now()
        sync.response = status
        sync.save(update_fields=["status", "error", "finished_at", "response", "updated_at"])
        _stamp(sync.link, "failed")
    elif attempt < 10 and not _eager(self):
        poll_menu_sync.apply_async(args=[str(sync.pk), attempt + 1], countdown=30)
    elif attempt >= 10:
        sync.status = "failed"
        sync.error = "Glovo did not report a result in time"
        sync.finished_at = timezone.now()
        sync.save(update_fields=["status", "error", "finished_at", "updated_at"])
        _stamp(sync.link, "failed")


def _stamp(link, status):
    RestaurantDeliveryPlatform.objects.filter(pk=link.pk).update(
        last_menu_sync_at=timezone.now(), last_menu_sync_status=status
    )


def _eager(task) -> bool:
    return bool(getattr(task.app.conf, "task_always_eager", False))
