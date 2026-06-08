# Terms & Conditions acceptance at checkout

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0017_add_security_amount'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='accepted_terms',
            field=models.BooleanField(
                default=False,
                help_text='User accepted rental Terms & Conditions at checkout',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='accepted_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text='When the user accepted the Terms & Conditions',
            ),
        ),
    ]
