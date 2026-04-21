from django.db import migrations

TIERS = [
    {
        "slug": "gourmand",
        "display_order": 0,
        "name_ka": "გურმანი",
        "name_en": "Gourmand",
        "name_ru": "Гурман",
        "min_points": 0,
        "discount_percent": 0,
    },
    {
        "slug": "silver",
        "display_order": 1,
        "name_ka": "ვერცხლი",
        "name_en": "Silver",
        "name_ru": "Серебро",
        "min_points": 200,
        "discount_percent": 5,
    },
    {
        "slug": "gold",
        "display_order": 2,
        "name_ka": "ოქრო",
        "name_en": "Gold",
        "name_ru": "Золото",
        "min_points": 500,
        "discount_percent": 10,
    },
    {
        "slug": "platinum",
        "display_order": 3,
        "name_ka": "პლატინა",
        "name_en": "Platinum",
        "name_ru": "Платина",
        "min_points": 1500,
        "discount_percent": 15,
    },
]


def seed_tiers(apps, schema_editor):
    Tier = apps.get_model("loyalty", "PlatformLoyaltyTier")
    for row in TIERS:
        Tier.objects.update_or_create(slug=row["slug"], defaults=row)


def remove_tiers(apps, schema_editor):
    Tier = apps.get_model("loyalty", "PlatformLoyaltyTier")
    Tier.objects.filter(slug__in=[t["slug"] for t in TIERS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("loyalty", "0002_platformloyaltytier_platformloyaltyledger"),
    ]

    operations = [
        migrations.RunPython(seed_tiers, remove_tiers),
    ]
