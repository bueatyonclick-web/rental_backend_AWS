# Generated migration: UserDevice for FCM push notifications (order accepted)

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0008_servicesubcategory_service_subcategory'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserDevice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fcm_token', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='devices', to='backend.user')),
            ],
            options={
                'verbose_name': 'User device (FCM)',
                'verbose_name_plural': 'User devices (FCM)',
            },
        ),
        migrations.AddConstraint(
            model_name='userdevice',
            constraint=models.UniqueConstraint(fields=('user', 'fcm_token'), name='backend_userdevice_user_fcm_token_unique'),
        ),
    ]
