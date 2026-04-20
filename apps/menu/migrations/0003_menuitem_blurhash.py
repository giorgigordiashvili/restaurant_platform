# Adds inline LQIP BlurHash strings to menu images.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("menu", "0002_alter_menucategorytranslation_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="menucategory",
            name="image_blurhash",
            field=models.CharField(
                blank=True,
                default="",
                help_text="BlurHash string for the image — used as an inline LQIP.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="menuitem",
            name="image_blurhash",
            field=models.CharField(
                blank=True,
                default="",
                help_text="BlurHash string for the image — used as an inline LQIP.",
                max_length=64,
            ),
        ),
    ]
