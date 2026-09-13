from django.db import migrations

TZ = "Asia/Tbilisi"
CRON_TASKS = [
    ("House accounts: monthly statements", "houseaccounts.monthly_statements", "0", "6", "Build and send last month's statement on each account's billing day."),
]


def seed(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    for name, task, minute, hour, description in CRON_TASKS:
        cron, _ = CrontabSchedule.objects.get_or_create(
            minute=minute, hour=hour, day_of_week="*", day_of_month="*", month_of_year="*", timezone=TZ
        )
        PeriodicTask.objects.update_or_create(
            name=name, defaults={"crontab": cron, "interval": None, "task": task, "enabled": True, "description": description}
        )


def unseed(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name__in=[t[0] for t in CRON_TASKS]).delete()


class Migration(migrations.Migration):
    dependencies = [("houseaccounts", "0001_initial"), ("django_celery_beat", "0019_alter_periodictasks_options")]

    operations = [migrations.RunPython(seed, unseed)]
