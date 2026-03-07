# Generated migration for ArtistAvailability

import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0010_adminnotificationlog'),
    ]

    operations = [
        migrations.CreateModel(
            name='ArtistAvailability',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('service_type', models.CharField(default='makeup', help_text='e.g. makeup, mehndi', max_length=50)),
                ('date', models.DateField()),
                ('status', models.CharField(choices=[('blocked', 'Blocked'), ('booked', 'Booked'), ('available', 'Available')], default='available', max_length=20)),
                ('notes', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('artist', models.ForeignKey(help_text='Service/Artist (e.g. Radha Makeup Artist)', on_delete=django.db.models.deletion.CASCADE, related_name='artist_availability', to='backend.service')),
            ],
            options={
                'verbose_name': 'Artist availability',
                'verbose_name_plural': 'Artist availability',
                'ordering': ['artist', 'date'],
                'unique_together': {('artist', 'date')},
            },
        ),
    ]
