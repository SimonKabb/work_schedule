from calendar import monthrange
from datetime import date, timedelta

from work_schedule.models import (
    User,
    Duty,
    ShiftType,
    ScheduleMonth,
    DutyDatePreference,
    PartTimeWorkload,
)


class ScheduleGenerator:

    def __init__(self, schedule_month: ScheduleMonth):
        self.schedule_month = schedule_month

        # данные
        self.users = []
        self.shift_types = []

        # словари
        self.preferences = {}
        self.target_hours = {}
        self.current_hours = {}

        # будущие дежурства
        self.generated_duties = []

    def load_data(self):
        self.users = list(User.objects.all())
        self.shift_types = list(ShiftType.objects.all())
        preferences = DutyDatePreference.objects.filter(
            month=self.schedule_month
            )
        self.preferences = {}

        for preference in preferences:
            key = (
                preference.user_id,
                preference.date,
            )
        self.preferences[key] = preference.status
        
        for user in self.users:
            if user.employee_type == User.EmployeeType.MAIN:

                self.target_hours[user.id] = (
                self.schedule_month.main_employee_hours
                )
            else:
                workload = (
                PartTimeWorkload.objects.get(
                    month=self.schedule_month,
                    user=user,
                    ).first()
                )
                self.target_hours[user.id] = workload.hours if workload else 0
            self.current_hours[user.id] = 0

    def generate_day(self, current_date):
        for shift_type in self.shift_types:
            employee = self.choose_employee(
            current_date,
            shift_type
            )

        if employee:
            self.assign_employee(
                employee,
                current_date,
                shift_type
            )

   
    def generate(self):
        self.load_data()

        year = self.schedule_month.year
        month = self.schedule_month.month

        days_count = monthrange(year, month)[1]

        for day_number in range(1, days_count + 1):

            current_date = date(
                year,
                month,
                day_number
            )

            self.generate_day(current_date)

        self.save()
    
    def choose_employee(
        self,
        current_date,
        shift_type
    ):

        return self.users[0]
    