import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0007_backfill_ledger"),
        ("tenants", "0016_cash_module"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="restaurant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="payments", to="tenants.restaurant"
            ),
        ),
    ]
