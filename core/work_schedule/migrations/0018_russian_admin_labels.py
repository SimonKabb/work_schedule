from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("work_schedule", "0017_scheduleholiday"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="duty",
            options={"verbose_name": "Смена сотрудника", "verbose_name_plural": "Смены сотрудников"},
        ),
        migrations.AlterModelOptions(
            name="dutydatepreference",
            options={"verbose_name": "Предпочтение по дате", "verbose_name_plural": "Предпочтения по датам"},
        ),
        migrations.AlterModelOptions(
            name="parttimeworkload",
            options={"verbose_name": "Норма часов совместителя", "verbose_name_plural": "Нормы часов совместителей"},
        ),
        migrations.AlterModelOptions(
            name="preferenceactivity",
            options={"verbose_name": "Заполнение предпочтений", "verbose_name_plural": "Заполнение предпочтений"},
        ),
        migrations.AlterModelOptions(
            name="schedulemonth",
            options={"verbose_name": "Месяц графика", "verbose_name_plural": "Месяцы графика"},
        ),
        migrations.AlterModelOptions(
            name="shiftrequirement",
            options={"verbose_name": "Требование к смене", "verbose_name_plural": "Требования к сменам"},
        ),
        migrations.AlterModelOptions(
            name="shifttype",
            options={"verbose_name": "Тип смены", "verbose_name_plural": "Типы смен"},
        ),
        migrations.AlterModelOptions(
            name="user",
            options={"verbose_name": "Пользователь", "verbose_name_plural": "Пользователи"},
        ),
        migrations.AlterField(
            model_name="duty",
            name="date",
            field=models.DateField(verbose_name="Дата"),
        ),
        migrations.AlterField(
            model_name="duty",
            name="generated",
            field=models.BooleanField(default=False, verbose_name="Создана генератором"),
        ),
        migrations.AlterField(
            model_name="duty",
            name="shift_type",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="work_schedule.shifttype", verbose_name="Тип смены"),
        ),
        migrations.AlterField(
            model_name="duty",
            name="user",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL, verbose_name="Сотрудник"),
        ),
        migrations.AlterField(
            model_name="dutydatepreference",
            name="date",
            field=models.DateField(verbose_name="Дата"),
        ),
        migrations.AlterField(
            model_name="dutydatepreference",
            name="month",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="preferences", to="work_schedule.schedulemonth", verbose_name="Месяц графика"),
        ),
        migrations.AlterField(
            model_name="dutydatepreference",
            name="status",
            field=models.CharField(choices=[("AVAILABLE", "Может"), ("UNAVAILABLE", "Не может")], max_length=11, verbose_name="Предпочтение"),
        ),
        migrations.AlterField(
            model_name="dutydatepreference",
            name="user",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="duty_date_preferences", to=settings.AUTH_USER_MODEL, verbose_name="Сотрудник"),
        ),
        migrations.AlterField(
            model_name="employeeabsence",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, verbose_name="Добавлено"),
        ),
        migrations.AlterField(
            model_name="employeeabsence",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, verbose_name="Изменено"),
        ),
        migrations.AlterField(
            model_name="parttimeworkload",
            name="hours",
            field=models.PositiveSmallIntegerField(verbose_name="Норма часов"),
        ),
        migrations.AlterField(
            model_name="parttimeworkload",
            name="month",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="part_time_hours", to="work_schedule.schedulemonth", verbose_name="Месяц графика"),
        ),
        migrations.AlterField(
            model_name="parttimeworkload",
            name="user",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL, verbose_name="Сотрудник"),
        ),
        migrations.AlterField(
            model_name="preferenceactivity",
            name="month",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="preference_activities", to="work_schedule.schedulemonth", verbose_name="Месяц графика"),
        ),
        migrations.AlterField(
            model_name="preferenceactivity",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, verbose_name="Обновлено"),
        ),
        migrations.AlterField(
            model_name="preferenceactivity",
            name="user",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="preference_activities", to=settings.AUTH_USER_MODEL, verbose_name="Сотрудник"),
        ),
        migrations.AlterField(
            model_name="schedulemonth",
            name="main_employee_hours",
            field=models.PositiveSmallIntegerField(verbose_name="Норма часов основного сотрудника"),
        ),
        migrations.AlterField(
            model_name="schedulemonth",
            name="month",
            field=models.PositiveSmallIntegerField(verbose_name="Месяц"),
        ),
        migrations.AlterField(
            model_name="schedulemonth",
            name="year",
            field=models.PositiveSmallIntegerField(verbose_name="Год"),
        ),
        migrations.AlterField(
            model_name="shiftrequirement",
            name="date",
            field=models.DateField(verbose_name="Дата"),
        ),
        migrations.AlterField(
            model_name="shiftrequirement",
            name="month",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="requirements", to="work_schedule.schedulemonth", verbose_name="Месяц графика"),
        ),
        migrations.AlterField(
            model_name="shiftrequirement",
            name="required_people",
            field=models.PositiveSmallIntegerField(default=1, verbose_name="Требуется сотрудников"),
        ),
        migrations.AlterField(
            model_name="shiftrequirement",
            name="shift_type",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="work_schedule.shifttype", verbose_name="Тип смены"),
        ),
        migrations.AlterField(
            model_name="shifttype",
            name="hours",
            field=models.PositiveSmallIntegerField(verbose_name="Часы"),
        ),
        migrations.AlterField(
            model_name="shifttype",
            name="locked",
            field=models.BooleanField(default=False, verbose_name="Зафиксирована"),
        ),
        migrations.AlterField(
            model_name="shifttype",
            name="name",
            field=models.CharField(max_length=100, verbose_name="Название"),
        ),
        migrations.AlterField(
            model_name="shifttype",
            name="required_people",
            field=models.PositiveSmallIntegerField(default=1, verbose_name="Требуется сотрудников"),
        ),
        migrations.AlterField(
            model_name="user",
            name="employee_type",
            field=models.CharField(choices=[("MAIN", "Основной"), ("PART", "Совместитель")], default="MAIN", max_length=4, verbose_name="Тип сотрудника"),
        ),
        migrations.AlterField(
            model_name="user",
            name="full_name",
            field=models.CharField(max_length=255, verbose_name="ФИО"),
        ),
    ]
