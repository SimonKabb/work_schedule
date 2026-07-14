import uuid

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class User(AbstractUser):
    class EmployeeType(models.TextChoices):
        MAIN = "MAIN", "Основной"
        PART_TIME = "PART", "Совместитель"

    full_name = models.CharField(max_length=255)

    employee_type = models.CharField(
        max_length=4,
        choices=EmployeeType.choices,
        default=EmployeeType.MAIN,
    )

    def __str__(self):
        return self.full_name


class Team(models.Model):
    name = models.CharField(max_length=255, unique=True, verbose_name="Название")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    registration_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name="Токен регистрации",
    )

    class Meta:
        ordering = ("name",)
        verbose_name = "Коллектив"
        verbose_name_plural = "Коллективы"

    def __str__(self):
        return self.name


class TeamMembership(models.Model):
    class Role(models.TextChoices):
        MANAGER = "MANAGER", "Заведующий"
        EMPLOYEE = "EMPLOYEE", "Сотрудник"

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="Коллектив",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="team_memberships",
        verbose_name="Пользователь",
    )
    role = models.CharField(
        max_length=8,
        choices=Role.choices,
        default=Role.EMPLOYEE,
        verbose_name="Роль",
    )
    participates_in_schedule = models.BooleanField(
        default=True,
        verbose_name="Участвует в графике",
        help_text="Заведующий может управлять коллективом, не участвуя в сменах.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("team", "user"),
                name="unique_team_membership",
            )
        ]
        ordering = ("team__name", "user__full_name")
        verbose_name = "Участник коллектива"
        verbose_name_plural = "Участники коллективов"

    def __str__(self):
        return f"{self.user} — {self.team} ({self.get_role_display()})"


class ScheduleMonth(models.Model):
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="schedule_months",
        verbose_name="Коллектив",
    )
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()

    # Все основные сотрудники должны столько отработать
    main_employee_hours = models.PositiveSmallIntegerField()
    increased_staff_weekdays = models.CharField(
        max_length=13,
        default="1,4",
        verbose_name="Дни с увеличенным составом",
        help_text="Дни недели в служебном формате, выбираются в форме администратора.",
    )
    increased_staff_count = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(1)],
        verbose_name="Сотрудников в выбранные дни",
    )
    part_time_allowed_weekdays = models.CharField(
        max_length=13,
        default="1,4",
        verbose_name="Разрешённые дни совместителей",
        help_text="Дни недели в служебном формате, выбираются в форме администратора.",
    )
    part_time_hours_percent = models.PositiveSmallIntegerField(
        default=50,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        verbose_name="Норма совместителя, %",
    )

    @staticmethod
    def _weekday_set(value):
        return {
            int(item)
            for item in value.split(",")
            if item.strip().isdigit() and 0 <= int(item) <= 6
        }

    @property
    def increased_staff_weekday_set(self):
        return self._weekday_set(self.increased_staff_weekdays)

    @property
    def part_time_allowed_weekday_set(self):
        return self._weekday_set(self.part_time_allowed_weekdays)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("team", "year", "month"),
                name="unique_schedule_month_per_team",
            )
        ]
    def __str__(self):
        return f"{self.team}: {self.month:02d}.{self.year}"


class ScheduleHoliday(models.Model):
    month = models.ForeignKey(
        ScheduleMonth,
        on_delete=models.CASCADE,
        related_name="holidays",
        verbose_name="Месяц графика",
    )
    date = models.DateField(verbose_name="Праздничная дата")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("month", "date"),
                name="unique_schedule_holiday_date",
            )
        ]
        ordering = ("date",)
        verbose_name = "Праздничный день"
        verbose_name_plural = "Праздничные дни"

    def clean(self):
        super().clean()
        if self.date and self.month_id and (
            self.date.year != self.month.year or self.date.month != self.month.month
        ):
            raise ValidationError({
                "date": "Праздничная дата должна находиться внутри выбранного месяца."
            })

    def __str__(self):
        return f"{self.date:%d.%m.%Y} — как выходной"

class DutyDatePreference(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Может"
        UNAVAILABLE = "UNAVAILABLE", "Не может"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="duty_date_preferences",
    )
    date = models.DateField()
    
    month = models.ForeignKey(
        ScheduleMonth,
        on_delete=models.CASCADE,
        related_name="preferences"
    )

    status = models.CharField(
        max_length=11,
        choices=Status.choices,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("month", "user", "date"),
                name="unique_month_user_preference_date",
            )
        ]


class PreferenceActivity(models.Model):
    """Records that an employee has updated preferences for a schedule month."""
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="preference_activities",
    )
    month = models.ForeignKey(
        ScheduleMonth,
        on_delete=models.CASCADE,
        related_name="preference_activities",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "month"],
                name="unique_preference_activity_per_month",
            )
        ]


class EmployeeAbsence(models.Model):
    class Type(models.TextChoices):
        VACATION = "VACATION", "Отпуск"
        SICK_LEAVE = "SICK", "Больничный"
        TRAINING = "TRAINING", "Обучение"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="absences",
        verbose_name="Сотрудник",
    )
    date = models.DateField(verbose_name="Дата")
    absence_type = models.CharField(
        max_length=8,
        choices=Type.choices,
        default=Type.VACATION,
        verbose_name="Причина отсутствия",
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_absences",
        verbose_name="Кто добавил",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "date"),
                name="unique_employee_absence_date",
            )
        ]
        ordering = ("date", "user__full_name")
        verbose_name = "Отсутствие сотрудника"
        verbose_name_plural = "Отсутствия сотрудников"

    def __str__(self):
        return f"{self.user}: {self.get_absence_type_display()} {self.date:%d.%m.%Y}"

class PartTimeWorkload(models.Model):
    # Количество часов, которое совместитель должен отработать в конкретный месяц
    month = models.ForeignKey(
        ScheduleMonth,
        on_delete=models.CASCADE,
        related_name="part_time_hours",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
    )
    hours = models.PositiveSmallIntegerField()
    class Meta:
        unique_together = ("month", "user")

class ShiftType(models.Model):
    class DayType(models.TextChoices):
        WEEKDAY = "WEEKDAY", "Будни"
        WEEKEND = "WEEKEND", "Выходные"

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="shift_types",
        verbose_name="Коллектив",
    )
    # Тип смены, например: "День", "Ночь", "Выходной"
    name = models.CharField(max_length=100)
    required_people = models.PositiveSmallIntegerField(default=1)
    hours = models.PositiveSmallIntegerField()
    day_type = models.CharField(
        max_length=7,
        choices=DayType.choices,
        default=DayType.WEEKDAY,
        verbose_name="Дни применения",
    )
    locked = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} ({self.hours}h)"
    
class Duty(models.Model):
    # Смена, назначенная конкретному пользователю на конкретный день месяца
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name="duties",
        verbose_name="Коллектив",
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateField()
    shift_type = models.ForeignKey(ShiftType, on_delete=models.PROTECT)
    generated = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "user", "date"],
                name="unique_team_user_duty_per_day",
            )
        ]
    @property
    def hours(self):
        return self.shift_type.hours

class ShiftRequirement(models.Model):
    # Требуемое количество сотрудников на смену и какие смены должны быть в конкретный день месяца
    month = models.ForeignKey(
        ScheduleMonth,
        on_delete=models.CASCADE,
        related_name="requirements",
    )
    date = models.DateField()
    shift_type = models.ForeignKey(
        ShiftType,
        on_delete=models.CASCADE,
    )
    required_people = models.PositiveSmallIntegerField(default=1)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["date", "shift_type"],
                name="unique_shift_requirement",
            )
        ]
