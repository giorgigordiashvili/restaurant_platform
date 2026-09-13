from celery import shared_task

from apps.delivery.courier.base import CourierError


def _eager(task) -> bool:
    from django.conf import settings

    return bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)) or task.request.called_directly


@shared_task(bind=True, name="ordering.request_courier", max_retries=5, ignore_result=True)
def request_courier(self, delivery_id: str):
    from apps.ordering import dispatch
    from apps.ordering.models import Delivery

    try:
        delivery = Delivery.objects.select_related("order__restaurant").get(pk=delivery_id)
    except Delivery.DoesNotExist:
        return "missing"
    if delivery.status != "requested" or delivery.external_id:
        return "skipped"
    try:
        dispatch.run_platform_request(delivery)
    except CourierError as exc:
        if exc.retryable and not _eager(self):
            raise self.retry(exc=exc, countdown=min(30 * (2**self.request.retries), 600))
        dispatch._fail(delivery, str(exc), payload=exc.payload)
        return "failed"
    return delivery.status


@shared_task(name="ordering.refresh_deliveries", ignore_result=True)
def refresh_deliveries():
    from apps.ordering import dispatch
    from apps.ordering.models import Delivery

    n = 0
    for d in (
        Delivery.objects.filter(status__in=Delivery.OPEN)
        .exclude(provider="own")
        .exclude(external_id="")
        .select_related("order__restaurant", "restaurant")
    ):
        if dispatch.refresh(d):
            n += 1
    return n


@shared_task(name="ordering.check_domains", ignore_result=True)
def check_domains():
    from apps.ordering import domains
    from apps.ordering.models import RestaurantDomain

    n = 0
    for d in RestaurantDomain.objects.all():
        if domains.verify(d):
            n += 1
    return n
