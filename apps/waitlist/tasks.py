from celery import shared_task


@shared_task(name="waitlist.auto_expire", ignore_result=True)
def auto_expire():
    from apps.waitlist import services

    return services.auto_expire()


@shared_task(name="waitlist.daily_close", ignore_result=True)
def daily_close():
    from apps.waitlist import services

    return services.daily_close()
