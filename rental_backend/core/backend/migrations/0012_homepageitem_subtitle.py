# Add subtitle to HomePageItem for Featured Collection section header

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0011_artistavailability'),
    ]

    operations = [
        migrations.AddField(
            model_name='homepageitem',
            name='subtitle',
            field=models.CharField(blank=True, default='', help_text='Optional subtitle (e.g. Handpicked styles for you)', max_length=200),
        ),
    ]
