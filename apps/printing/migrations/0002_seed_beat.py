"""Beat row: put jobs a crashed bridge left in 'printing' back in the queue."""

from django.db import migrations

NAME = "Printing: requeue stale jobs"


def seed(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    interval, _ = IntervalSchedule.objects.get_or_create(every=1, period="minutes")
    PeriodicTask.objects.update_or_create(
        name=NAME,
        defaults={
            "interval": interval,
            "crontab": None,
            "task": "printing.requeue_stale_jobs",
            "enabled": True,
            "description": "Jobs claimed by a bridge more than 2 minutes ago without an acknowledgement go back to the queue.",
        },
    )


def unseed(apps, schema_editor):
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(name=NAME).delete()


class Migration(migrations.Migration):
    dependencies = [("printing", "0001_initial"), ("django_celery_beat", "0019_alter_periodictasks_options")]

    operations = [migrations.RunPython(seed, unseed)]
