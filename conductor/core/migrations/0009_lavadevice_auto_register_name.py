# Generated by Django 3.1.7 on 2021-04-22 12:04

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_lavadevice_ota_started'),
    ]

    operations = [
        migrations.AddField(
            model_name='lavadevice',
            name='auto_register_name',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
