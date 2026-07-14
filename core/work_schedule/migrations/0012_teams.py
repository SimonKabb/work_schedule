from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def move_existing_data_to_default_team(apps, schema_editor):
    Team = apps.get_model("work_schedule", "Team")
    TeamMembership = apps.get_model("work_schedule", "TeamMembership")
    User = apps.get_model("work_schedule", "User")
    ScheduleMonth = apps.get_model("work_schedule", "ScheduleMonth")
    ShiftType = apps.get_model("work_schedule", "ShiftType")
    Duty = apps.get_model("work_schedule", "Duty")

    team = Team.objects.create(name="Основной коллектив")
    TeamMembership.objects.bulk_create(
        [
            TeamMembership(
                team=team,
                user=user,
                role="EMPLOYEE",
                participates_in_schedule=True,
            )
            for user in User.objects.filter(is_employee=True)
        ]
    )
    ScheduleMonth.objects.update(team=team)
    ShiftType.objects.update(team=team)
    Duty.objects.update(team=team)


class Migration(migrations.Migration):
    dependencies = [
        ("work_schedule", "0011_user_is_employee"),
    ]

    operations = [
        migrations.CreateModel(
            name="Team",
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
                ("name", models.CharField(max_length=255, unique=True, verbose_name="Название")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активен")),
            ],
            options={
                "verbose_name": "Коллектив",
                "verbose_name_plural": "Коллективы",
                "ordering": ("name",),
            },
        ),
        migrations.CreateModel(
            name="TeamMembership",
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
                (
                    "role",
                    models.CharField(
                        choices=[("MANAGER", "Заведующий"), ("EMPLOYEE", "Сотрудник")],
                        default="EMPLOYEE",
                        max_length=8,
                        verbose_name="Роль",
                    ),
                ),
                (
                    "participates_in_schedule",
                    models.BooleanField(
                        default=True,
                        help_text="Заведующий может управлять коллективом, не участвуя в сменах.",
                        verbose_name="Участвует в графике",
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="work_schedule.team",
                        verbose_name="Коллектив",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="team_memberships",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Пользователь",
                    ),
                ),
            ],
            options={
                "verbose_name": "Участник коллектива",
                "verbose_name_plural": "Участники коллективов",
                "ordering": ("team__name", "user__full_name"),
            },
        ),
        migrations.AddField(
            model_name="schedulemonth",
            name="team",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="schedule_months",
                to="work_schedule.team",
                verbose_name="Коллектив",
            ),
        ),
        migrations.AddField(
            model_name="shifttype",
            name="team",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="shift_types",
                to="work_schedule.team",
                verbose_name="Коллектив",
            ),
        ),
        migrations.AddField(
            model_name="duty",
            name="team",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="duties",
                to="work_schedule.team",
                verbose_name="Коллектив",
            ),
        ),
        migrations.RunPython(move_existing_data_to_default_team, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="schedulemonth",
            name="team",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="schedule_months",
                to="work_schedule.team",
                verbose_name="Коллектив",
            ),
        ),
        migrations.AlterField(
            model_name="shifttype",
            name="team",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="shift_types",
                to="work_schedule.team",
                verbose_name="Коллектив",
            ),
        ),
        migrations.AlterField(
            model_name="duty",
            name="team",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="duties",
                to="work_schedule.team",
                verbose_name="Коллектив",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="schedulemonth",
            unique_together=set(),
        ),
        migrations.RemoveConstraint(
            model_name="duty",
            name="unique_user_duty_per_day",
        ),
        migrations.AddConstraint(
            model_name="teammembership",
            constraint=models.UniqueConstraint(
                fields=("team", "user"),
                name="unique_team_membership",
            ),
        ),
        migrations.AddConstraint(
            model_name="schedulemonth",
            constraint=models.UniqueConstraint(
                fields=("team", "year", "month"),
                name="unique_schedule_month_per_team",
            ),
        ),
        migrations.AddConstraint(
            model_name="duty",
            constraint=models.UniqueConstraint(
                fields=("team", "user", "date"),
                name="unique_team_user_duty_per_day",
            ),
        ),
    ]
