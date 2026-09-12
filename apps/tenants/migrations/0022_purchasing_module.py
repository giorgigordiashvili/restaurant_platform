from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenants", "0021_promotions_module")]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="purchasing_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Purchasing module: suppliers, price lists, purchase orders from the buy list, receiving.",
            ),
        ),
    ]
