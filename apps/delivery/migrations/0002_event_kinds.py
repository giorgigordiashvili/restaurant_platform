from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("delivery", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="deliveryplatformevent",
            name="kind",
            field=models.CharField(
                choices=[
                    ("order_created", "Order received"),
                    ("order_cancelled", "Order cancelled by platform"),
                    ("status_pushed", "Status pushed"),
                    ("menu_status", "Menu sync status"),
                    ("cancel_requested", "Cancellation requested by restaurant"),
                    ("order_status", "Order status notification"),
                    ("refund_pushed", "Refund pushed"),
                    ("store_status", "Store paused / resumed"),
                ],
                max_length=30,
            ),
        ),
    ]
