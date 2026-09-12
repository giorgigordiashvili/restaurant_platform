from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0019_delivery_module")]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="notifications_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Notifications module: staff push / in-app alerts, guest SMS and email.",
            ),
        ),
    ]
