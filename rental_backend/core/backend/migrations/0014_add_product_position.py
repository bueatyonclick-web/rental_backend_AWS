# Add position field to Product for category-wise display order

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0013_home_banners'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='position',
            field=models.IntegerField(default=9999, help_text='Display order within category (lower = first)'),
        ),
    ]
