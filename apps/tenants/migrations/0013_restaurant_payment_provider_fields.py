# Generated for the Flitt + split-payment work.
#
# `makemigrations` also emitted a pile of unrelated parler translation-Meta
# AlterField operations (see CLAUDE.md note: "Django migration gotcha — check
# deps before pushing"). Those have been stripped so this migration only
# ships the five Restaurant fields introduced for payments.

import django.core.validators
from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0012_restaurant_accepts_platform_loyalty"),
    ]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="accepts_bog_payments",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Accept card payments via Bank of Georgia. Requires a "
                    "valid BOG payout IBAN below. Net of the platform's "
                    "commission is split to the IBAN automatically on "
                    "settlement."
                ),
            ),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="bog_payout_iban",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Georgian IBAN (GE## + bank code + 16 digits) that "
                    "receives the restaurant's share."
                ),
                max_length=34,
                validators=[
                    django.core.validators.RegexValidator(
                        message=(
                            "Must be a Georgian IBAN in the form "
                            "GE##XX################."
                        ),
                        regex="^GE\\d{2}[A-Z]{2}\\d{16}$",
                    )
                ],
            ),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="accepts_flitt_payments",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Accept card payments via Flitt. Requires the "
                    "restaurant's Flitt sub-merchant id below. The platform "
                    "charges the full amount to its master Flitt account "
                    "and settles the restaurant's share via a follow-up "
                    "/api/settlement call."
                ),
            ),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="flitt_sub_merchant_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Flitt sub-merchant id provided by Flitt after onboarding.",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="platform_commission_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("5.00"),
                help_text=(
                    "Superuser-only. Percentage the platform keeps on every "
                    "paid order."
                ),
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0")),
                    django.core.validators.MaxValueValidator(Decimal("100")),
                ],
            ),
        ),
    ]
