"""Global units of measure. Conversions only within a dimension (mass / volume / count)."""

from django.db import migrations

UNITS = [
    # code, name, dimension, factor to base, order
    ("g", "gram", "mass", "1", 1),
    ("kg", "kilogram", "mass", "1000", 2),
    ("mg", "milligram", "mass", "0.001", 3),
    ("ml", "millilitre", "volume", "1", 1),
    ("l", "litre", "volume", "1000", 2),
    ("pcs", "piece", "count", "1", 1),
]


def seed(apps, schema_editor):
    Unit = apps.get_model("inventory", "UnitOfMeasure")
    for code, name, dimension, factor, order in UNITS:
        Unit.objects.update_or_create(
            code=code,
            defaults={"name": name, "dimension": dimension, "factor_to_base": factor, "display_order": order},
        )


def unseed(apps, schema_editor):
    Unit = apps.get_model("inventory", "UnitOfMeasure")
    Unit.objects.filter(code__in=[u[0] for u in UNITS]).delete()


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_warehouse")]

    operations = [migrations.RunPython(seed, unseed)]
