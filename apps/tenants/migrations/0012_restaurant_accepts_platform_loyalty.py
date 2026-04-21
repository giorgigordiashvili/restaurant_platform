from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0011_city_id_bigint"),
    ]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="accepts_platform_loyalty",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Opt into the platform-wide tier discount program. When on, "
                    "customer orders at this restaurant accrue platform loyalty "
                    "points and any existing tier discount the customer holds is "
                    "applied to their subtotal. Restaurant absorbs the discount."
                ),
            ),
        ),
    ]
