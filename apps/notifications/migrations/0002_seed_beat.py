from django.db import migrations

TZ = "Asia/Tbilisi"
CRON_TASKS = [
    ("Notifications: prune old", "notifications.prune", "30", "3", "Delete in-app notifications older than 90 days."),
]
INTERVAL_TASKS = [
    ("Notifications: reservation reminders", "notifications.reservation_reminders", 60, "Remind guests of upcoming reservations."),
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
    for name, task, minutes, description in INTERVAL_TASKS:
        interval, _ = IntervalSchedule.objects.get_or_create(every=minutes, period="minutes")
        PeriodicTask.objects.update_or_create(
            name=name, defaults={"interval": interval, "crontab": None, "task": task, "enabled": True, "description": description}
        )


def unseed(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name__in=[t[0] for t in CRON_TASKS] + [t[0] for t in INTERVAL_TASKS]).delete()


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial"), ("django_celery_beat", "0019_alter_periodictasks_options")]

    operations = [migrations.RunPython(seed, unseed)]
