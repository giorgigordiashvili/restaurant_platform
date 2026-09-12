from django.db import migrations

NAME = "Fiscal: retry failed documents"


def seed(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    interval, _ = IntervalSchedule.objects.get_or_create(every=10, period="minutes")
    PeriodicTask.objects.update_or_create(
        name=NAME,
        defaults={"interval": interval, "crontab": None, "task": "fiscal.retry_failed", "enabled": True, "description": "Re-issue fiscal documents whose provider call failed."},
    )


def unseed(apps, schema_editor):
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(name=NAME).delete()


class Migration(migrations.Migration):
    dependencies = [("fiscal", "0002_profiles_from_tax_rate"), ("django_celery_beat", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
