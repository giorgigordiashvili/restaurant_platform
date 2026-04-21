# City.id was created as BigAutoField in migration 0007 but the model
# silently inherited UUIDField from TimeStampedModel afterwards, so the
# Python model and the on-disk `cities.id` column drifted apart. Saving
# a Restaurant with a City FK triggered
#   "column city_obj_id is of type bigint but expression is of type uuid".
# The model now declares BigAutoField explicitly; this migration keeps
# Django's migration graph in sync. No-op at the SQL level — the column
# is already bigint.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0010_restaurant_blurhash"),
    ]

    operations = [
        migrations.AlterField(
            model_name="city",
            name="id",
            field=models.BigAutoField(primary_key=True, serialize=False),
        ),
    ]
