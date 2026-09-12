from django.db import migrations, models


def existing_restaurants_off(apps, schema_editor):
    """Existing tenants keep working exactly as before until an owner switches the module on."""
    Restaurant = apps.get_model("tenants", "Restaurant")
    Restaurant.objects.update(cash_enabled=False)


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0015_modules"),
    ]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="cash_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Cash & payments module: cash shifts (open/close the till with an X/Z report), taking payments in the POS, discounts, comps, voids and refunds. Needs Ordering.",
            ),
        ),
        migrations.RunPython(existing_restaurants_off, migrations.RunPython.noop),
    ]
