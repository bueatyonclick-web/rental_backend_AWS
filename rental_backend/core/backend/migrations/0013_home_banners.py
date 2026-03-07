# Home Banner / Template for dynamic home page banners

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0012_homepageitem_subtitle'),
    ]

    operations = [
        migrations.CreateModel(
            name='HomeBanner',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(blank=True, max_length=200, null=True)),
                ('image', models.ImageField(upload_to='home_banners/')),
                ('redirect_type', models.CharField(
                    choices=[
                        ('product', 'Product'),
                        ('category', 'Category'),
                        ('external_link', 'External Link'),
                    ],
                    default='category',
                    help_text='product, category, or external_link',
                    max_length=20,
                )),
                ('redirect_value', models.CharField(
                    blank=True,
                    help_text='Product ID, category slug, or full URL for external_link',
                    max_length=500,
                    null=True,
                )),
                ('display_order', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['display_order', 'id'],
                'verbose_name': 'Home Banner',
                'verbose_name_plural': 'Home Banners',
            },
        ),
    ]
