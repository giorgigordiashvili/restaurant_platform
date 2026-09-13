from celery import shared_task


@shared_task(name="giftcards.expire", ignore_result=True)
def expire():
    from apps.giftcards import services

    return services.expire()
