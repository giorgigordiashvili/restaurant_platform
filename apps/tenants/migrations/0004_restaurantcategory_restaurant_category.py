# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0003_alter_restaurant_options_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='RestaurantCategory',
            fields=[
                ('id', models.UUIDField(default=None, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100, unique=True)),
                ('slug', models.SlugField(max_length=100, unique=True)),
                ('description', models.TextField(blank=True)),
                ('icon', models.CharField(blank=True, help_text="Material icon name (e.g., 'restaurant', 'local_cafe', 'fastfood')", max_length=50)),
                ('image', models.ImageField(blank=True, null=True, upload_to='restaurant_categories/')),
                ('display_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Restaurant Category',
                'verbose_name_plural': 'Restaurant Categories',
                'db_table': 'restaurant_categories',
                'ordering': ['display_order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='restaurant',
            name='category',
            field=models.ForeignKey(blank=True, help_text='Restaurant category (e.g., Italian, Fast Food)', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='restaurants', to='tenants.restaurantcategory'),
        ),
    ]
