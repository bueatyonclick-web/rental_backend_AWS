from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0019_referralsettings_user_ban_reason_user_device_id_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ServiceVendor',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('service_vendor_id', models.CharField(blank=True, help_text='Unique service vendor ID (e.g., SVCVEN001)', max_length=50, unique=True)),
                ('name', models.CharField(help_text='Service vendor name / business name', max_length=200)),
                ('phone', models.CharField(help_text='Phone used for OTP login', max_length=15, unique=True)),
                ('area', models.CharField(blank=True, default='', max_length=200)),
                ('pincode', models.CharField(blank=True, default='', max_length=10)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('service_subcategories', models.ManyToManyField(blank=True, related_name='service_vendors', to='backend.servicesubcategory')),
            ],
            options={
                'verbose_name': 'Service Vendor',
                'verbose_name_plural': 'Service Vendors',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ServiceVendorToken',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(max_length=500, unique=True)),
                ('fcmtoken', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('vendor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tokens_set', to='backend.servicevendor')),
            ],
            options={
                'verbose_name': 'Service Vendor Token',
                'verbose_name_plural': 'Service Vendor Tokens',
            },
        ),
        migrations.AddField(
            model_name='service',
            name='service_vendor',
            field=models.ForeignKey(blank=True, help_text='If set, this service is owned/managed by a service vendor', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='services_set', to='backend.servicevendor'),
        ),
    ]

