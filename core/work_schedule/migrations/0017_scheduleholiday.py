from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("work_schedule", "0016_employeeabsence_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ScheduleHoliday",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("date", models.DateField(verbose_name="Праздничная дата")),
                (
                    "month",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="holidays",
                        to="work_schedule.schedulemonth",
                        verbose_name="Месяц графика",
                    ),
                ),
            ],
            options={
                "verbose_name": "Праздничный день",
                "verbose_name_plural": "Праздничные дни",
                "ordering": ("date",),
            },
        ),
        migrations.AddConstraint(
            model_name="scheduleholiday",
            constraint=models.UniqueConstraint(
                fields=("month", "date"),
                name="unique_schedule_holiday_date",
            ),
        ),
    ]
