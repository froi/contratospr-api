# Generated by Django 2.1.3 on 2018-11-18 15:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("contracts", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="servicegroup",
            name="name",
            field=models.CharField(max_length=255, unique=True),
        )
    ]