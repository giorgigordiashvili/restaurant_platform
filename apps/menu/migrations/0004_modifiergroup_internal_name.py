# Hand-trimmed: makemigrations also emitted the long-standing "Change Meta
# options" drift for the parler translation models (see CLAUDE.md); only the
# new field ships here.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("menu", "0003_menuitem_blurhash"),
    ]

    operations = [
        migrations.AddField(
            model_name="modifiergroup",
            name="internal_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Only staff see this. Use it to tell similar groups apart, e.g. 'ქათმის ხვეულა - ექსტრა'.",
                max_length=150,
            ),
        ),
    ]
