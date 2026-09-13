from django.db import migrations, models
from django.utils import timezone


def mark_existing_complete(apps, schema_editor):
    """Restaurants that exist today were configured by hand -- don't send them through the wizard."""
    Restaurant = apps.get_model("tenants", "Restaurant")
    Restaurant.objects.filter(setup_completed_at__isnull=True).update(setup_completed_at=timezone.now())


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0028_gift_house_modules"),
    ]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="setup_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="setup_state",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name="RestaurantSetup",
            fields=[],
            options={
                "verbose_name": "Setup wizard",
                "verbose_name_plural": "Setup wizard",
                "proxy": True,
                "indexes": [],
                "constraints": [],
            },
            bases=("tenants.restaurant",),
        ),
        migrations.RunPython(mark_existing_complete, migrations.RunPython.noop),
    ]
