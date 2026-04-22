# Adds wallet_applied to Order — discount-like field that calculate_totals subtracts.

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0005_order_server_order_tip_amount_order_tip_distribution"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="wallet_applied",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text=(
                    "Wallet balance the customer spent on this order. Discount-like; "
                    "calculate_totals subtracts it. Actual debit happens at payment success."
                ),
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
    ]
