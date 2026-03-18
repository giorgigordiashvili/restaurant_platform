"""
Add City model with translations and city_obj ForeignKey to Restaurant.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0006_add_translations_to_category_and_amenity"),
    ]

    operations = [
        # Create City table
        migrations.CreateModel(
            name="City",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("slug", models.SlugField(max_length=100, unique=True)),
                ("country", models.CharField(default="Georgia", max_length=100)),
                ("display_order", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "City",
                "verbose_name_plural": "Cities",
                "db_table": "cities",
                "ordering": ["display_order", "slug"],
            },
        ),
        # Create City translation table
        migrations.CreateModel(
            name="CityTranslation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "language_code",
                    models.CharField(db_index=True, max_length=15, verbose_name="Language"),
                ),
                ("name", models.CharField(max_length=100)),
                (
                    "master",
                    models.ForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="translations",
                        to="tenants.city",
                    ),
                ),
            ],
            options={
                "verbose_name": "City Translation",
                "db_table": "cities_translation",
                "unique_together": {("language_code", "master")},
                "managed": True,
            },
        ),
        # Add city_obj FK to Restaurant
        migrations.AddField(
            model_name="restaurant",
            name="city_obj",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="restaurants",
                to="tenants.city",
                verbose_name="City",
            ),
        ),
    ]
