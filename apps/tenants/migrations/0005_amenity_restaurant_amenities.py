# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0004_restaurantcategory_restaurant_category'),
    ]

    operations = [
        migrations.CreateModel(
            name='Amenity',
            fields=[
                ('id', models.UUIDField(default=None, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100, unique=True)),
                ('slug', models.SlugField(max_length=100, unique=True)),
                ('icon', models.CharField(blank=True, help_text="Material icon name (e.g., 'deck', 'music_note', 'wifi')", max_length=50)),
                ('description', models.TextField(blank=True)),
                ('display_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Amenity',
                'verbose_name_plural': 'Amenities',
                'db_table': 'amenities',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='restaurant',
            name='amenities',
            field=models.ManyToManyField(blank=True, help_text='Amenities available at the restaurant', related_name='restaurants', to='tenants.amenity'),
        ),
    ]
