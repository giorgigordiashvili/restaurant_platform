from django.db import migrations

INTERVAL_TASKS = [
    ("Terminals: expire stale transactions", "terminals.expire_stale", 60, "Time out card payments nobody answered."),
    ("Terminals: poll pay-by-link", "terminals.poll_open", 30, "Ask BOG / TBC about open pay-by-link payments without a callback."),
]


def seed(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    for name, task, seconds, description in INTERVAL_TASKS:
        interval, _ = IntervalSchedule.objects.get_or_create(every=seconds, period="seconds")
        PeriodicTask.objects.update_or_create(
            name=name, defaults={"interval": interval, "crontab": None, "task": task, "enabled": True, "description": description}
        )


def unseed(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name__in=[t[0] for t in INTERVAL_TASKS]).delete()


class Migration(migrations.Migration):
    dependencies = [("terminals", "0001_initial"), ("django_celery_beat", "0019_alter_periodictasks_options")]

    operations = [migrations.RunPython(seed, unseed)]
