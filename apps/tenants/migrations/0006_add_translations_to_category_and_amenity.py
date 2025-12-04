# Generated manually - adds translation support to RestaurantCategory and Amenity

import django.db.models.deletion
import parler.models
from django.db import migrations, models


def migrate_category_data_forward(apps, schema_editor):
    """Migrate existing name/description to translation table."""
    from django.db import connection

    # Check if the old columns still exist
    with connection.cursor() as cursor:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'restaurant_categories'")
        columns = [row[0] for row in cursor.fetchall()]

    if 'name' not in columns:
        # Old columns already removed, skip data migration
        return

    RestaurantCategoryTranslation = apps.get_model('tenants', 'RestaurantCategoryTranslation')

    with connection.cursor() as cursor:
        cursor.execute("SELECT id, name, description FROM restaurant_categories")
        rows = cursor.fetchall()

    for row in rows:
        cat_id, name, description = row
        RestaurantCategoryTranslation.objects.get_or_create(
            master_id=cat_id,
            language_code='ka',
            defaults={
                'name': name or f'Category {cat_id}',
                'description': description or '',
            }
        )


def migrate_amenity_data_forward(apps, schema_editor):
    """Migrate existing name/description to translation table."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'amenities'")
        columns = [row[0] for row in cursor.fetchall()]

    if 'name' not in columns:
        return

    AmenityTranslation = apps.get_model('tenants', 'AmenityTranslation')

    with connection.cursor() as cursor:
        cursor.execute("SELECT id, name, description FROM amenities")
        rows = cursor.fetchall()

    for row in rows:
        amenity_id, name, description = row
        AmenityTranslation.objects.get_or_create(
            master_id=amenity_id,
            language_code='ka',
            defaults={
                'name': name or f'Amenity {amenity_id}',
                'description': description or '',
            }
        )


def noop(apps, schema_editor):
    """No-op for reverse migration."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0005_amenity_restaurant_amenities'),
    ]

    operations = [
        # Step 1: Create RestaurantCategoryTranslation table
        migrations.CreateModel(
            name='RestaurantCategoryTranslation',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'language_code',
                    models.CharField(db_index=True, max_length=15, verbose_name='Language'),
                ),
                ('name', models.CharField(max_length=100)),
                ('description', models.TextField(blank=True)),
                (
                    'master',
                    models.ForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='translations',
                        to='tenants.restaurantcategory',
                    ),
                ),
            ],
            options={
                'db_table': 'restaurant_categories_translation',
                'verbose_name': 'Restaurant Category Translation',
                'managed': True,
                'default_permissions': (),
                'unique_together': {('language_code', 'master')},
            },
        ),

        # Step 2: Create AmenityTranslation table
        migrations.CreateModel(
            name='AmenityTranslation',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'language_code',
                    models.CharField(db_index=True, max_length=15, verbose_name='Language'),
                ),
                ('name', models.CharField(max_length=100)),
                ('description', models.TextField(blank=True)),
                (
                    'master',
                    models.ForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='translations',
                        to='tenants.amenity',
                    ),
                ),
            ],
            options={
                'db_table': 'amenities_translation',
                'verbose_name': 'Amenity Translation',
                'managed': True,
                'default_permissions': (),
                'unique_together': {('language_code', 'master')},
            },
        ),

        # Step 3: Migrate data from old columns to translation tables
        migrations.RunPython(migrate_category_data_forward, noop),
        migrations.RunPython(migrate_amenity_data_forward, noop),

        # Step 4: Remove old name and description columns from RestaurantCategory
        migrations.RemoveField(
            model_name='restaurantcategory',
            name='name',
        ),
        migrations.RemoveField(
            model_name='restaurantcategory',
            name='description',
        ),

        # Step 5: Remove old name and description columns from Amenity
        migrations.RemoveField(
            model_name='amenity',
            name='name',
        ),
        migrations.RemoveField(
            model_name='amenity',
            name='description',
        ),

        # Step 6: Update model meta options
        migrations.AlterModelOptions(
            name='restaurantcategory',
            options={
                'ordering': ['display_order'],
                'verbose_name': 'Restaurant Category',
                'verbose_name_plural': 'Restaurant Categories',
            },
        ),
        migrations.AlterModelOptions(
            name='amenity',
            options={
                'ordering': ['display_order'],
                'verbose_name': 'Amenity',
                'verbose_name_plural': 'Amenities',
            },
        ),
    ]
