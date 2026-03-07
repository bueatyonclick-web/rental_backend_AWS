# Generated migration for Male/Female home tile images

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0006_category_gender'),
    ]

    operations = [
        migrations.CreateModel(
            name='HomeGenderTileImage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('male_tile_image', models.ImageField(blank=True, help_text='Image for the Male category tile on home page', null=True, upload_to='home_tiles/')),
                ('female_tile_image', models.ImageField(blank=True, help_text='Image for the Female category tile on home page', null=True, upload_to='home_tiles/')),
            ],
            options={
                'verbose_name': 'Home Male/Female tile images',
                'verbose_name_plural': 'Home Male/Female tile images',
            },
        ),
    ]
