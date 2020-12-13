# Generated by Django 3.1.4 on 2020-12-13 19:12

import django.core.validators
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('django_ca', '0020_auto_20201213_1719'),
    ]

    operations = [
        migrations.AlterField(
            model_name='acmeaccount',
            name='ca',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='django_ca.certificateauthority', verbose_name='Certificate Authority'),
        ),
        migrations.AlterField(
            model_name='acmeaccount',
            name='kid',
            field=models.URLField(unique=True, validators=[django.core.validators.URLValidator(schemes=('http', 'https'))], verbose_name='Key ID'),
        ),
        migrations.AlterField(
            model_name='acmeauthorization',
            name='order',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='authorizations', to='django_ca.acmeorder'),
        ),
        migrations.AlterField(
            model_name='acmechallenge',
            name='auth',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='challenges', to='django_ca.acmeauthorization'),
        ),
        migrations.AlterField(
            model_name='acmeorder',
            name='account',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='orders', to='django_ca.acmeaccount'),
        ),
    ]
