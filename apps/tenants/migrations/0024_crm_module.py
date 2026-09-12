from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0023_timekeeping_module")]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="crm_enabled",
            field=models.BooleanField(
                default=False,
                help_text="CRM & marketing module: guest records, segments, SMS / email campaigns and automations.",
            ),
        ),
    ]
