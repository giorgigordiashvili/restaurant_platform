from celery import shared_task


@shared_task(name="terminals.expire_stale", ignore_result=True)
def expire_stale():
    from apps.terminals import services

    return services.expire_stale()


@shared_task(name="terminals.poll_open", ignore_result=True)
def poll_open():
    from apps.terminals import services

    return services.poll_open()
