from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0022_purchasing_module")]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="timekeeping_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Timekeeping module: clock in / out on the POS, weekly rota, hours report.",
            ),
        ),
    ]
