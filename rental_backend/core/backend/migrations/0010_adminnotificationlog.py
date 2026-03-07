# Generated migration for AdminNotificationLog

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0009_userdevice'),
    ]

    operations = [
        migrations.CreateModel(
            name='AdminNotificationLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255)),
                ('body', models.TextField()),
                ('target_type', models.CharField(choices=[('all', 'All users'), ('selected', 'Selected users')], max_length=20)),
                ('target_count', models.PositiveIntegerField(default=0, help_text='Number of devices/users targeted')),
                ('data', models.JSONField(blank=True, help_text='Optional data payload for deep linking (e.g. {"screen": "orders"})', null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Admin push notification log',
                'verbose_name_plural': 'Admin push notification logs',
                'ordering': ['-created_at'],
            },
        ),
    ]
