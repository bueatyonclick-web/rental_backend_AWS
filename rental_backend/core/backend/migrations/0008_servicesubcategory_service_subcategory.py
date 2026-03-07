# Generated migration: Service sub-categories (e.g. Decoration -> Bridal Entry, Haldi, Mehendi, Sangeet)

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('backend', '0007_homegendertileimage'),
    ]

    operations = [
        migrations.CreateModel(
            name='ServiceSubCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150)),
                ('position', models.IntegerField(default=0)),
                ('image', models.ImageField(blank=True, null=True, upload_to='service_subcategories/')),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='subcategories', to='backend.servicecategory')),
            ],
            options={
                'verbose_name': 'Service Sub-category',
                'verbose_name_plural': 'Service Sub-categories',
                'ordering': ['category', 'position'],
            },
        ),
        migrations.AddField(
            model_name='service',
            name='subcategory',
            field=models.ForeignKey(blank=True, help_text="Optional: assign to a sub-category (e.g. Bridal Entry, Haldi). Category can be set from sub-category.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='services_set', to='backend.servicesubcategory'),
        ),
        migrations.AddConstraint(
            model_name='servicesubcategory',
            constraint=models.UniqueConstraint(fields=('category', 'name'), name='backend_servicesubcategory_category_name_unique'),
        ),
    ]
