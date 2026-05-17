from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("spa_app", "0004_employeeprofile_restore_fields_and_simple_schedule"),
    ]

    operations = [
        migrations.AddField(
            model_name="review",
            name="appointment",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="review",
                to="spa_app.appointment",
                verbose_name="Запись",
            ),
        ),
    ]
