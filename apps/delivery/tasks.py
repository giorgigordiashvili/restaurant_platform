import logging

from django.utils import timezone

from celery import shared_task

from apps.delivery import services
from apps.delivery.errors import PlatformClientError
from apps.delivery.models import DeliveryPlatformEvent, PlatformMenuSync, RestaurantDeliveryPlatform
from apps.orders.models import Order

logger = logging.getLogger(__name__)


@shared_task(name="delivery.sync_status", bind=True, max_retries=5, ignore_result=True, acks_late=True)
def sync_status(self, order_id, platform_status):
    order = Order.objects.select_related("restaurant").filter(pk=order_id).first()
    if order is None:
        return
    try:
        services.push_status(order, platform_status)
    except PlatformClientError as exc:
        if exc.retryable and self.request.retries < self.max_retries and not _eager(self):
            raise self.retry(countdown=10 * (2**self.request.retries))
        logger.error("Giving up pushing %s for order %s: %s", platform_status, order_id, exc)


@shared_task(name="delivery.wolt_fetch_order", bind=True, max_retries=5, ignore_result=True, acks_late=True)
def wolt_fetch_order(self, link_id, order_id, event_id=None):
    link = RestaurantDeliveryPlatform.objects.select_related("restaurant").filter(pk=link_id).first()
    if link is None:
        return
    event = DeliveryPlatformEvent.objects.filter(pk=event_id).first() if event_id else None
    try:
        services.fetch_wolt_order(link, order_id, event=event)
    except PlatformClientError as exc:
        if event is not None:
            event.error = str(exc)
            event.save(update_fields=["error", "updated_at"])
        if exc.retryable and self.request.retries < self.max_retries and not _eager(self):
            raise self.retry(countdown=5 * (2**self.request.retries))
        logger.error("Giving up fetching Wolt order %s: %s", order_id, exc)
    except Exception as exc:  # noqa: BLE001 - config errors land on the event row
        logger.exception("Wolt order %s could not be imported", order_id)
        if event is not None:
            event.error = str(exc)[:1000]
            event.save(update_fields=["error", "updated_at"])


@shared_task(name="delivery.refund_items", bind=True, max_retries=5, ignore_result=True, acks_late=True)
def refund_items(self, order_id, items, ref):
    order = Order.objects.select_related("restaurant").filter(pk=order_id).first()
    if order is None:
        return
    try:
        services.push_refund(order, items, ref)
    except PlatformClientError as exc:
        if exc.retryable and self.request.retries < self.max_retries and not _eager(self):
            raise self.retry(countdown=10 * (2**self.request.retries))
        logger.error("Giving up refunding %s on order %s: %s", items, order_id, exc)


@shared_task(name="delivery.push_menu", bind=True, max_retries=3, ignore_result=True)
def push_menu(self, link_id, sync_id):
    link = RestaurantDeliveryPlatform.objects.select_related("restaurant").filter(pk=link_id).first()
    sync = PlatformMenuSync.objects.filter(pk=sync_id).first()
    if link is None or sync is None:
        return
    sync.status = "sent"
    sync.started_at = timezone.now()
    try:
        if link.platform == "glovo":
            from apps.delivery.glovo.menu import build_menu

            url = services.menu_feed_url(link)
            sync.request = {**sync.request, "menuUrl": url}
            sync.save(update_fields=["status", "started_at", "request", "updated_at"])
            sync.product_count = len(build_menu(link)["products"])
            response = services.client_for(link).upload_menu(url)
        elif link.platform == "wolt":
            from apps.delivery.wolt.menu import build_menu

            sync.save(update_fields=["status", "started_at", "updated_at"])
            menu = build_menu(link)
            sync.product_count = sum(len(c["items"]) for c in menu["categories"])
            sync.request = {**sync.request, "categories": len(menu["categories"])}
            response = services.client_for(link).push_menu(menu)
        else:
            raise services.DeliveryError("not_implemented", "No API integration for this platform.")
    except Exception as exc:  # noqa: BLE001 - recorded on the sync row
        sync.status = "failed"
        sync.error = str(exc)[:1000]
        sync.finished_at = timezone.now()
        sync.save(update_fields=["status", "error", "finished_at", "product_count", "request", "updated_at"])
        _stamp(link, "failed")
        return
    sync.transaction_id = str(response.get("transactionId") or response.get("transaction_id") or "")
    sync.response = response
    sync.status = "processing" if sync.transaction_id else "success"
    sync.save(update_fields=["transaction_id", "response", "status", "product_count", "request", "updated_at"])
    if sync.transaction_id:
        poll_menu_sync.apply_async(args=[str(sync.pk), 0], countdown=30 if not _eager(self) else 0)
    else:
        sync.finished_at = timezone.now()
        sync.save(update_fields=["finished_at", "updated_at"])
        _stamp(link, "success")


@shared_task(name="delivery.push_menu_updates", bind=True, max_retries=3, ignore_result=True)
def push_menu_updates(self, link_id, sync_id):
    """Prices + availability of the whole menu (cheaper than a full push; Glovo bulk-update / Wolt item PATCH)."""
    link = RestaurantDeliveryPlatform.objects.select_related("restaurant").filter(pk=link_id).first()
    sync = PlatformMenuSync.objects.filter(pk=sync_id).first()
    if link is None or sync is None:
        return
    sync.status = "sent"
    sync.started_at = timezone.now()
    sync.save(update_fields=["status", "started_at", "updated_at"])
    try:
        client = services.client_for(link)
        if link.platform == "glovo":
            from apps.delivery.glovo.menu import build_bulk_refresh

            body = build_bulk_refresh(link.restaurant)
            sync.product_count = len(body["products"])
            response = client.bulk_update(**body)
        elif link.platform == "wolt":
            from apps.delivery.wolt.menu import build_bulk_refresh

            body = build_bulk_refresh(link.restaurant)
            sync.product_count = len(body["items"])
            response = {"items": client.update_items(body["items"]) if body["items"] else {}}
            if body["options"]:
                response["options"] = client.update_option_values(body["options"])
        else:
            raise services.DeliveryError("not_implemented", "No API integration for this platform.")
    except Exception as exc:  # noqa: BLE001
        sync.status = "failed"
        sync.error = str(exc)[:1000]
        sync.finished_at = timezone.now()
        sync.save(update_fields=["status", "error", "finished_at", "product_count", "updated_at"])
        return
    sync.status = "success"
    sync.response = response
    sync.transaction_id = str(response.get("transactionId") or "") if isinstance(response, dict) else ""
    sync.finished_at = timezone.now()
    sync.save(update_fields=["status", "response", "transaction_id", "finished_at", "product_count", "updated_at"])


@shared_task(name="delivery.poll_menu_sync", bind=True, ignore_result=True)
def poll_menu_sync(self, sync_id, attempt=0):
    sync = PlatformMenuSync.objects.select_related("link__restaurant").filter(pk=sync_id).first()
    if sync is None or sync.status not in ("processing", "sent"):
        return
    try:
        status = services.client_for(sync.link).menu_upload_status(sync.transaction_id)
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
