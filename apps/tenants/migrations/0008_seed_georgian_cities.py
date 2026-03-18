"""
Seed Georgian cities with ka/en translations and migrate existing restaurant city data.
"""

from django.db import migrations


CITIES = [
    {"slug": "tbilisi", "country": "Georgia", "order": 1, "ka": "თბილისი", "en": "Tbilisi"},
    {"slug": "batumi", "country": "Georgia", "order": 2, "ka": "ბათუმი", "en": "Batumi"},
    {"slug": "kutaisi", "country": "Georgia", "order": 3, "ka": "ქუთაისი", "en": "Kutaisi"},
    {"slug": "rustavi", "country": "Georgia", "order": 4, "ka": "რუსთავი", "en": "Rustavi"},
    {"slug": "gori", "country": "Georgia", "order": 5, "ka": "გორი", "en": "Gori"},
    {"slug": "zugdidi", "country": "Georgia", "order": 6, "ka": "ზუგდიდი", "en": "Zugdidi"},
    {"slug": "poti", "country": "Georgia", "order": 7, "ka": "ფოთი", "en": "Poti"},
    {"slug": "telavi", "country": "Georgia", "order": 8, "ka": "თელავი", "en": "Telavi"},
    {"slug": "samtredia", "country": "Georgia", "order": 9, "ka": "სამტრედია", "en": "Samtredia"},
    {"slug": "senaki", "country": "Georgia", "order": 10, "ka": "სენაკი", "en": "Senaki"},
    {"slug": "akhaltsikhe", "country": "Georgia", "order": 11, "ka": "ახალციხე", "en": "Akhaltsikhe"},
    {"slug": "kobuleti", "country": "Georgia", "order": 12, "ka": "ქობულეთი", "en": "Kobuleti"},
    {"slug": "ozurgeti", "country": "Georgia", "order": 13, "ka": "ოზურგეთი", "en": "Ozurgeti"},
    {"slug": "marneuli", "country": "Georgia", "order": 14, "ka": "მარნეული", "en": "Marneuli"},
    {"slug": "kaspi", "country": "Georgia", "order": 15, "ka": "კასპი", "en": "Kaspi"},
    {"slug": "chiatura", "country": "Georgia", "order": 16, "ka": "ჭიათურა", "en": "Chiatura"},
    {"slug": "tskhinvali", "country": "Georgia", "order": 17, "ka": "ცხინვალი", "en": "Tskhinvali"},
    {"slug": "khashuri", "country": "Georgia", "order": 18, "ka": "ხაშური", "en": "Khashuri"},
    {"slug": "zestaponi", "country": "Georgia", "order": 19, "ka": "ზესტაფონი", "en": "Zestaponi"},
    {"slug": "borjomi", "country": "Georgia", "order": 20, "ka": "ბორჯომი", "en": "Borjomi"},
    {"slug": "mtskheta", "country": "Georgia", "order": 21, "ka": "მცხეთა", "en": "Mtskheta"},
    {"slug": "sighnaghi", "country": "Georgia", "order": 22, "ka": "სიღნაღი", "en": "Sighnaghi"},
    {"slug": "mestia", "country": "Georgia", "order": 23, "ka": "მესტია", "en": "Mestia"},
    {"slug": "stepantsminda", "country": "Georgia", "order": 24, "ka": "სტეფანწმინდა", "en": "Stepantsminda"},
    {"slug": "gudauri", "country": "Georgia", "order": 25, "ka": "გუდაური", "en": "Gudauri"},
]


def seed_cities(apps, schema_editor):
    City = apps.get_model("tenants", "City")
    CityTranslation = apps.get_model("tenants", "CityTranslation")
    Restaurant = apps.get_model("tenants", "Restaurant")

    city_map = {}  # en_name_lower -> city instance

    for c in CITIES:
        city, _ = City.objects.get_or_create(
            slug=c["slug"],
            defaults={
                "country": c["country"],
                "display_order": c["order"],
                "is_active": True,
            },
        )

        # Add ka translation
        CityTranslation.objects.get_or_create(
            master=city,
            language_code="ka",
            defaults={"name": c["ka"]},
        )

        # Add en translation
        CityTranslation.objects.get_or_create(
            master=city,
            language_code="en",
            defaults={"name": c["en"]},
        )

        city_map[c["en"].lower()] = city
        city_map[c["ka"]] = city

    # Migrate existing restaurants' city text to city_obj
    for restaurant in Restaurant.objects.filter(city_obj__isnull=True).exclude(city=""):
        city_text = restaurant.city.strip()
        matched = city_map.get(city_text.lower()) or city_map.get(city_text)
        if matched:
            restaurant.city_obj = matched
            restaurant.save(update_fields=["city_obj"])


def reverse_seed(apps, schema_editor):
    City = apps.get_model("tenants", "City")
    City.objects.filter(slug__in=[c["slug"] for c in CITIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0007_add_city_model"),
    ]

    operations = [
        migrations.RunPython(seed_cities, reverse_seed),
    ]
