from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("work_schedule", "0019_team_schedule_rules"),
    ]

    operations = [
        migrations.AlterField(
            model_name="team",
            name="schedule_rules",
            field=models.CharField(
                choices=[
                    ("DOCTORS", "Врачи"),
                    ("NURSES", "Одна суточная смена"),
                ],
                default="DOCTORS",
                help_text=(
                    "Для врачей используются обычные и усиленные дни. Для медсестёр "
                    "назначается один сотрудник в сутки и используются только 23-часовые смены."
                ),
                max_length=7,
                verbose_name="Правила графика",
            ),
        ),
    ]
