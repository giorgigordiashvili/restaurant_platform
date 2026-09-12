from celery import shared_task

from apps.timekeeping import services


@shared_task(name="timekeeping.auto_close", ignore_result=True)
def auto_close():
    return services.auto_close_stale()


@shared_task(name="timekeeping.shift_reminders", ignore_result=True)
def shift_reminders():
    return services.send_shift_reminders()
