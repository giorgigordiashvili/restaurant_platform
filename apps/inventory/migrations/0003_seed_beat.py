"""
Seed the django-celery-beat rows for the warehouse jobs. The rows live in
the DB (DatabaseScheduler) so platform admin can retune them from /admin/.
"""

from django.db import migrations

TZ = "Asia/Tbilisi"

CRON_TASKS = [
    # name, task, minute, hour, description
    ("Warehouse: expire lots", "inventory.expire_lots_nightly", "0", "2", "Write off lots past their expiry date."),
    ("Warehouse: expiring-soon alerts", "inventory.expiring_soon_alerts", "10", "2", "Open alerts for lots expiring soon."),
    ("Warehouse: low stock + buy list", "inventory.low_stock_nightly", "20", "2", "Low-stock alerts and tomorrow's buy list."),
]
INTERVAL_TASKS = [
    ("Warehouse: release stale payment holds", "inventory.release_stale_payment_reservations", 10, "Release ingredient holds of unpaid checkouts older than 30 minutes."),
    ("Warehouse: reconcile order reservations", "inventory.reconcile_order_reservations", 15, "Consume / release holds for orders whose status changed without the hook."),
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
    dependencies = [("inventory", "0002_seed_units"), ("django_celery_beat", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
