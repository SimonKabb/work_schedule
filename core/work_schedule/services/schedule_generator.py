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
from ortools.sat.python import cp_model

class ScheduleGenerator:
    
    def __init__(self, schedule_month):

        self.schedule_month = schedule_month

        self.model = cp_model.CpModel()
        self.solver = cp_model.CpSolver()

        self.users = []
        self.shift_types = []

        self.preferences = {}

        self.target_hours = {}

        # переменные:
        # (user_id, дата, shift_id) -> BoolVar
        self.variables = {}

        # фактические часы каждого сотрудника
        # user_id -> IntVar
        self.worked_hours = {}

        self.generated_duties = []
        self.all_shift_vars = []
        self.users_map = {}
        self.shift_map = {}

    def load_data(self):

        self.users = list(User.objects.all())

        self.shift_types = list(ShiftType.objects.all())
        
        self.users_map = {
            user.id: user
            for user in self.users
        }

        self.shift_map = {
            shift.id: shift
            for shift in self.shift_types
        }

        preferences = DutyDatePreference.objects.filter(
            month=self.schedule_month
        )

        for pref in preferences:
            self.preferences[
                (pref.user_id, pref.date)
            ] = pref.status

        for user in self.users:

            if user.employee_type == User.EmployeeType.MAIN:

                self.target_hours[user.id] = (
                    self.schedule_month.main_employee_hours
                )

            else:

                workload = (
                    PartTimeWorkload.objects
                    .filter(
                        month=self.schedule_month,
                        user=user
                    )
                    .first()
                )

                self.target_hours[user.id] = (
                    workload.hours if workload else 0
                )
    
    
    def create_variables(self):

        year = self.schedule_month.year
        month = self.schedule_month.month

        days = monthrange(year, month)[1]


        # создаем переменные смен

        for day in range(1, days + 1):

            current_date = date(
                year,
                month,
                day
            )

            for user in self.users:
                for shift in self.shift_types:
                    var = self.model.NewBoolVar(
                        f"user_{user.id}_date_{day}_shift_{shift.id}"
                    )

                    self.variables[
                        (
                            user.id,
                            current_date,
                            shift.id,
                        )
                    ] = var

                    self.all_shift_vars.append(var)
        for user in self.users:

            self.worked_hours[user.id] = (
                self.model.NewIntVar(
                    0,
                    self.target_hours[user.id],
                    f"hours_user_{user.id}"
                )
            )

        print(
            "Создано переменных:",
            len(self.variables)
            )
    def add_one_shift_per_day_constraint(self):

        year = self.schedule_month.year
        month = self.schedule_month.month

        days = monthrange(year, month)[1]

        for day in range(1, days + 1):

            current_date = date(year, month, day)

            for user in self.users:

                vars = []

                for shift in self.shift_types:

                    vars.append(
                        self.variables[
                            (
                                user.id,
                                current_date,
                                shift.id,
                            )
                        ]
                    )

                self.model.Add(sum(vars) <= 1)   

    def add_required_people_constraint(self):

        year = self.schedule_month.year
        month = self.schedule_month.month

        days = monthrange(year, month)[1]

        for day in range(1, days + 1):

            current_date = date(year, month, day)

            for shift in self.shift_types:

                vars = []

                for user in self.users:

                    vars.append(
                        self.variables[
                            (
                                user.id,
                                current_date,
                                shift.id,
                            )
                        ]
                    )

                self.model.Add(
                    sum(vars) <= shift.required_people
                )

    def add_preferences_constraint(self):

        year = self.schedule_month.year
        month = self.schedule_month.month

        days = monthrange(year, month)[1]

        for day in range(1, days + 1):

            current_date = date(year, month, day)

            for user in self.users:

                status = self.preferences.get(
                    (
                        user.id,
                        current_date,
                    )
                )

                if status != DutyDatePreference.Status.UNAVAILABLE:
                    continue

                for shift in self.shift_types:

                    self.model.Add(
                        self.variables[
                            (
                                user.id,
                                current_date,
                                shift.id,
                            )
                        ] == 0
                    )
    def add_hours_constraint(self):

        for user in self.users:

            vars = []

            coeffs = []

            year = self.schedule_month.year
            month = self.schedule_month.month

            days = monthrange(year, month)[1]

            for day in range(1, days + 1):

                current_date = date(year, month, day)

                for shift in self.shift_types:

                    vars.append(
                        self.variables[
                            (
                                user.id,
                                current_date,
                                shift.id,
                            )
                        ]
                    )

                    coeffs.append(
                        shift.hours
                    )

            self.model.Add(
                sum(
                    v * h
                    for v, h in zip(vars, coeffs)
                ) <= self.target_hours[user.id]
            )
    
    def add_worked_hours_constraint(self):

        year = self.schedule_month.year
        month = self.schedule_month.month

        days = monthrange(year, month)[1]


        for user in self.users:

            hours = []

            for day in range(1, days + 1):

                current_date = date(
                    year,
                    month,
                    day
                )

                for shift in self.shift_types:

                    hours.append(
                        self.variables[
                            (
                                user.id,
                                current_date,
                                shift.id,
                            )
                        ] * shift.hours
                    )


            self.model.Add(
                self.worked_hours[user.id]
                ==
                sum(hours)
            )    


    def solve(self):

        self.model.Maximize(
            sum(self.all_shift_vars)
        )

        status = self.solver.Solve(self.model)

        print(
            "Статус:",
            self.solver.StatusName(status)
        )

        for user in self.users:
            print(
                user.full_name,
                "часов:",
                self.solver.Value(
                    self.worked_hours[user.id]
                )
            )

        if status not in (
            cp_model.OPTIMAL,
            cp_model.FEASIBLE,
        ):
            raise Exception(
                "Невозможно построить график."
            )

        print("Решение найдено")


        self.generated_duties = []


        for key, var in self.variables.items():

            if self.solver.Value(var) == 0:
                continue

            user_id, duty_date, shift_id = key

            self.generated_duties.append(
                {
                    "user": self.users_map[user_id],
                    "date": duty_date,
                    "shift_type": self.shift_map[shift_id],
                }
            )


        print(
            "Всего найдено смен:",
            len(self.generated_duties)
        )


        for duty in self.generated_duties:
            print(
                duty["date"],
                duty["user"].full_name,
                duty["shift_type"].name
            )


        for user in self.users:
            print(
                user.full_name,
                self.solver.Value(
                    self.worked_hours[user.id]
                ),
                "часов"
            )

    def save(self):
        print(
            "Всего найдено смен:",
            len(self.generated_duties)
            )
        for duty in self.generated_duties:

            Duty.objects.create(
                user=duty["user"],
                date=duty["date"],
                shift_type=duty["shift_type"],
                generated=True,
            )
    def generate(self):

        self.load_data()

        self.create_variables()

        self.add_one_shift_per_day_constraint()

        self.add_required_people_constraint()

        self.add_preferences_constraint()

        self.add_hours_constraint()

        self.add_worked_hours_constraint()

        self.solve()

        self.save()