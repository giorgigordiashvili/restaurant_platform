"""
Seed a django-celery-beat PeriodicTask that runs
``payments.retry_failed_flitt_settlements`` once an hour.

The schedule lives in the DB (DatabaseScheduler) so the row is the source of
truth — platform admin can tweak the cadence or disable it from Django
/admin/ without shipping code. Using a data migration makes sure the row
is re-created automatically when staging gets rebuilt from scratch.
"""

from django.db import migrations

TASK_NAME = "Flitt: retry failed settlements"
DOTTED_TASK = "payments.retry_failed_flitt_settlements"


def seed(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    interval, _ = IntervalSchedule.objects.get_or_create(
        every=1,
        period="hours",
    )
    PeriodicTask.objects.update_or_create(
        name=TASK_NAME,
        defaults={
            "interval": interval,
            "task": DOTTED_TASK,
            "enabled": True,
            "description": (
                "Retries Flitt settlement calls for transactions whose "
                "approval webhook couldn't kick off /api/settlement "
                "inline (network flake, 5xx, etc.)."
            ),
        },
    )


def unseed(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=TASK_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        (
            "payments",
            "0004_alter_bogtransaction_flow_type_flitttransaction_and_more",
        ),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
