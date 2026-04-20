# Adds inline LQIP BlurHash strings to restaurant/category images.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0009_update_restaurant_data"),
    ]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="cover_image_blurhash",
            field=models.CharField(
                blank=True,
                default="",
                help_text="BlurHash string for the cover image — used as an inline LQIP.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="restaurant",
            name="logo_blurhash",
            field=models.CharField(
                blank=True,
                default="",
                help_text="BlurHash string for the logo — used as an inline LQIP.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="restaurantcategory",
            name="image_blurhash",
            field=models.CharField(
                blank=True,
                default="",
                help_text="BlurHash string for the image — used as an inline LQIP.",
                max_length=64,
            ),
        ),
    ]
