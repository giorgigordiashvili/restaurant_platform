# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tables', '0003_tablesession_host_tablesession_invite_code_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='tableqrcode',
            name='qr_image',
            field=models.ImageField(blank=True, help_text='Generated QR code image', null=True, upload_to='qr_codes/'),
        ),
    ]
