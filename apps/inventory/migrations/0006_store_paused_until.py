from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0005_delivery_platform_api")]

    operations = [
        migrations.AddField(
            model_name="restaurantdeliveryplatform",
            name="store_paused_until",
            field=models.DateTimeField(
                blank=True, help_text="We asked the platform to hide the store until then.", null=True
            ),
        ),
    ]
