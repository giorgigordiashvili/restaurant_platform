from django.db import migrations

TZ = "Asia/Tbilisi"
CRON_TASKS = [
    ("Waitlist: close yesterday's queue", "waitlist.daily_close", "0", "4", "Leftover walk-ins from earlier days are marked as left."),
]
INTERVAL_TASKS = [
    ("Waitlist: no-show notified guests", "waitlist.auto_expire", 120, "Notified guests who did not turn up become no-shows."),
]


def seed(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    for name, task, minute, hour, description in CRON_TASKS:
        cron, _ = CrontabSchedule.objects.get_or_create(
            minute=minute, hour=hour, day_of_week="*", day_of_month="*", month_of_year="*", timezone=TZ
        )
        PeriodicTask.objects.update_or_create(
            name=name, defaults={"crontab": cron, "interval": None, "task": task, "enabled": True, "description": description}
        )
    for name, task, seconds, description in INTERVAL_TASKS:
        interval, _ = IntervalSchedule.objects.get_or_create(every=seconds, period="seconds")
        PeriodicTask.objects.update_or_create(
            name=name, defaults={"interval": interval, "crontab": None, "task": task, "enabled": True, "description": description}
        )


def unseed(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name__in=[t[0] for t in CRON_TASKS] + [t[0] for t in INTERVAL_TASKS]).delete()


class Migration(migrations.Migration):
    dependencies = [("waitlist", "0001_initial"), ("django_celery_beat", "0019_alter_periodictasks_options")]

    operations = [migrations.RunPython(seed, unseed)]
