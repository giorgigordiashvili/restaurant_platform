from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0020_notifications_module")]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="promotions_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Promotions module: menu schedules, happy hours, promo codes, combos, '86 today'.",
            ),
        ),
    ]
