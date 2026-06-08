# Generated manually for coupon usage on service bookings

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0015_coupon_system'),
    ]

    operations = [
        migrations.AlterField(
            model_name='couponusage',
            name='order',
            field=models.ForeignKey(
                blank=True,
                help_text='Set when coupon was used on a product order',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='coupon_usages',
                to='backend.order',
            ),
        ),
        migrations.AddField(
            model_name='couponusage',
            name='service_booking',
            field=models.ForeignKey(
                blank=True,
                help_text='Set when coupon was used on a service booking',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='coupon_usages',
                to='backend.servicebooking',
            ),
        ),
    ]
