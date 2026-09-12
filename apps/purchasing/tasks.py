from celery import shared_task

from apps.purchasing import services


@shared_task(name="purchasing.due_orders", ignore_result=True)
def due_orders():
    return services.notify_due()
