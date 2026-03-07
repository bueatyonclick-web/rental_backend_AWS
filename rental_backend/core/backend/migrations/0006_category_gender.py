# Generated migration for Category.gender (Male/Female categories)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0005_alter_orderedproduct_rental_duration_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='gender',
            field=models.CharField(
                choices=[('male', 'Male'), ('female', 'Female')],
                default='female',
                help_text='Category for Male or Female (used in Rents section)',
                max_length=10,
            ),
        ),
    ]
