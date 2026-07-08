from django.contrib.auth.models import AbstractUser
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
    
class ScheduleMonth(models.Model):
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()

    # Все основные сотрудники должны столько отработать
    main_employee_hours = models.PositiveSmallIntegerField()

    class Meta:
        unique_together = ("year", "month") 
    def __str__(self):
        return str(self.year) + ', ' + str(self.month) 

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
        unique_together = ("user", "date")
    

class PartTimeWorkload(models.Model):
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
    name = models.CharField(max_length=100)
    required_people = models.PositiveSmallIntegerField(default=1)
    hours = models.PositiveSmallIntegerField()
    locked = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} ({self.hours}h)"
    
class Duty(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateField()
    shift_type = models.ForeignKey(ShiftType, on_delete=models.PROTECT)
    generated = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "date"],
                name="unique_user_duty_per_day",
            )
        ]

    @property
    def hours(self):
        return self.shift_type.hours
