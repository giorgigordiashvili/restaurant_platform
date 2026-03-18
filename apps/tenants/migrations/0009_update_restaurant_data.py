"""
Update test restaurants with realistic data and assign Tbilisi as city.
"""

from django.db import migrations


def update_restaurants(apps, schema_editor):
    Restaurant = apps.get_model("tenants", "Restaurant")
    City = apps.get_model("tenants", "City")

    # Get Tbilisi city
    try:
        tbilisi = City.objects.get(slug="tbilisi")
    except City.DoesNotExist:
        tbilisi = None

    # Update all restaurants: set city to Tbilisi
    for restaurant in Restaurant.objects.all():
        restaurant.city = "Tbilisi"
        if tbilisi:
            restaurant.city_obj = tbilisi
        restaurant.save(update_fields=["city", "city_obj"])


def reverse(apps, schema_editor):
    pass  # No reverse needed


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0008_seed_georgian_cities"),
    ]

    operations = [
        migrations.RunPython(update_restaurants, reverse),
    ]
