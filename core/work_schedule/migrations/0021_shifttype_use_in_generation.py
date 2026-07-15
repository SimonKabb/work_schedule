from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("work_schedule", "0020_alter_team_schedule_rules"),
    ]

    operations = [
        migrations.AddField(
            model_name="shifttype",
            name="use_in_generation",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Если выключено, смену можно назначать вручную, "
                    "но автоматический генератор выбирать её не будет."
                ),
                verbose_name="Использовать при генерации",
            ),
        ),
    ]
