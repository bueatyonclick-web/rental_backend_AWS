# Generated for security amount (refundable after product return)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0016_coupon_usage_service_booking'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='security_amount',
            field=models.IntegerField(
                default=0,
                help_text='Security amount in ₹ to be collected; refunded after product is received back',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='security_amount',
            field=models.IntegerField(
                default=0,
                help_text='Total security deposit in ₹ (refundable after product return)',
            ),
        ),
    ]
