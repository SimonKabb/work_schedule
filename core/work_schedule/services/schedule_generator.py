from calendar import monthrange
from datetime import date, timedelta

from django.db import transaction

from work_schedule.models import (
    User,
    Duty,
    ShiftType,
    Team,
    ScheduleMonth,
    DutyDatePreference,
    EmployeeAbsence,
    PartTimeWorkload,
    ScheduleHoliday,
)
from ortools.sat.python import cp_model


class ScheduleGenerationError(Exception):
    """The requested schedule cannot be generated from the configured data."""


class ScheduleGenerator:
    
    def __init__(self, schedule_month):

        self.schedule_month = schedule_month

        self.model = cp_model.CpModel()
        self.solver = cp_model.CpSolver()

        self.users = []
        self.weekday_shifts = []
        self.weekend_shifts = []

        self.preferences = {}
        self.absences = set()
        self.holidays = set()

        self.target_hours = {}
        # OR-Tools works with integers, so durations are stored as half-hour units.
        self.target_hour_units = {}
        self.manual_duties = []
        self.manual_duty_dates = set()
        self.manual_count_by_date = {}
        self.manual_part_time_count_by_date = {}
        self.manual_hour_units = {}
        self.worked_hour_units_upper = {}

        # переменные:
        # (user_id, дата, shift_id) -> BoolVar
        self.variables = {}

        # фактические часы каждого сотрудника
        # user_id -> IntVar
        self.worked_hour_units = {}

        self.generated_duties = []
        self.assignment_vars = []
        self.users_map = {}
        self.shift_map = {}

    @property
    def uses_nurse_rules(self):
        return self.schedule_month.team.schedule_rules == Team.ScheduleRules.NURSES

    def load_data(self):

        self.users = list(
            User.objects.filter(
                is_active=True,
                team_memberships__team=self.schedule_month.team,
                team_memberships__participates_in_schedule=True,
            ).distinct()
        )

        shifts = ShiftType.objects.filter(
            team=self.schedule_month.team,
            use_in_generation=True,
        )
        if self.uses_nurse_rules:
            nurse_shifts = list(shifts.filter(hours=23))
            self.weekday_shifts = [
                shift
                for shift in nurse_shifts
                if shift.day_type == ShiftType.DayType.WEEKDAY
            ] or nurse_shifts
            self.weekend_shifts = [
                shift
                for shift in nurse_shifts
                if shift.day_type == ShiftType.DayType.WEEKEND
            ] or nurse_shifts
        else:
            self.weekday_shifts = list(
                shifts.filter(day_type=ShiftType.DayType.WEEKDAY)
            )
            self.weekend_shifts = list(
                shifts.filter(day_type=ShiftType.DayType.WEEKEND)
            )

        if not self.weekday_shifts or not self.weekend_shifts:
            if self.uses_nurse_rules:
                raise ScheduleGenerationError(
                    "Для коллектива медсестёр создайте хотя бы одну 23-часовую смену."
                )
            raise ScheduleGenerationError(
                "Создайте хотя бы один тип смены для будней и один для выходных."
            )

        self.users_map = {
            user.id: user
            for user in self.users
        }
        self.shift_map = {
            shift.id: shift
            for shift in [*self.weekday_shifts, *self.weekend_shifts]
        }

        preferences = DutyDatePreference.objects.filter(
            month=self.schedule_month
        )

        for pref in preferences:
            self.preferences[
                (pref.user_id, pref.date)
            ] = pref.status

        self.absences = set(
            EmployeeAbsence.objects.filter(
                user__in=self.users,
                date__year=self.schedule_month.year,
                date__month=self.schedule_month.month,
            ).values_list("user_id", "date")
        )
        self.holidays = set(
            ScheduleHoliday.objects.filter(
                month=self.schedule_month,
            ).values_list("date", flat=True)
        )

        self.manual_duties = list(
            Duty.objects.filter(
                team=self.schedule_month.team,
                date__year=self.schedule_month.year,
                date__month=self.schedule_month.month,
                generated=False,
            ).select_related("user", "shift_type")
        )
        for duty in self.manual_duties:
            if self.uses_nurse_rules and duty.shift_type.hours != 23:
                raise ScheduleGenerationError(
                    "В графике медсестёр есть ручная смена длительностью не 23 часа. "
                    "Измените или удалите её перед генерацией."
                )
            self.manual_count_by_date[duty.date] = (
                self.manual_count_by_date.get(duty.date, 0) + 1
            )
            if duty.user.employee_type == User.EmployeeType.PART_TIME:
                self.manual_part_time_count_by_date[duty.date] = (
                    self.manual_part_time_count_by_date.get(duty.date, 0) + 1
                )
            if duty.user_id in self.users_map:
                self.manual_duty_dates.add((duty.user_id, duty.date))
                self.manual_hour_units[duty.user_id] = (
                    self.manual_hour_units.get(duty.user_id, 0) + duty.hour_units
                )

        if self.uses_nurse_rules:
            overloaded_dates = [
                duty_date
                for duty_date, count in self.manual_count_by_date.items()
                if count > 1
            ]
            if overloaded_dates:
                first_date = min(overloaded_dates)
                raise ScheduleGenerationError(
                    "В графике медсестёр на дату "
                    f"{first_date:%d.%m.%Y} вручную назначено больше одного сотрудника."
                )

        for user in self.users:

            if user.employee_type == User.EmployeeType.MAIN:

                self.target_hours[user.id] = self.schedule_month.main_employee_hours

            else:

                workload = (
                    PartTimeWorkload.objects
                    .filter(
                        month=self.schedule_month,
                        user=user
                    )
                    .first()
                )

                part_time_limit = (
                    self.schedule_month.main_employee_hours
                    * self.schedule_month.part_time_hours_percent
                    // 100
                )
                self.target_hours[user.id] = min(
                    workload.hours if workload else part_time_limit,
                    part_time_limit,
                )

            self.target_hour_units[user.id] = self.target_hours[user.id] * 2

            self.worked_hour_units_upper[user.id] = max(
                self.target_hour_units[user.id],
                self.manual_hour_units.get(user.id, 0),
            )
    
    
    def shifts_for_date(self, current_date):
        """Return all shift types allowed on this calendar date."""
        return (
            self.weekend_shifts
            if self.is_weekend_rule_date(current_date)
            else self.weekday_shifts
        )

    def is_weekend_rule_date(self, current_date):
        return current_date.weekday() >= 5 or current_date in self.holidays

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
                for shift in self.shifts_for_date(current_date):
                    var = self.model.NewBoolVar(
                        f"user_{user.id}_date_{day}_shift_{shift.id}"
                    )

                    self.variables[(user.id, current_date, shift.id)] = var
                    self.assignment_vars.append(var)
        for user in self.users:

            self.worked_hour_units[user.id] = (
                self.model.NewIntVar(
                    0,
                    self.worked_hour_units_upper[user.id],
                    f"hours_user_{user.id}"
                )
            )

        if not self.users:
            raise ScheduleGenerationError("Нельзя составить график: нет сотрудников.")

    def add_one_shift_per_day_constraint(self):

        year = self.schedule_month.year
        month = self.schedule_month.month

        days = monthrange(year, month)[1]

        for day in range(1, days + 1):

            current_date = date(year, month, day)

            for user in self.users:

                variables = [
                    self.variables[(user.id, current_date, shift.id)]
                    for shift in self.shifts_for_date(current_date)
                ]
                if (user.id, current_date) in self.manual_duty_dates:
                    self.model.Add(sum(variables) == 0)
                else:
                    self.model.Add(sum(variables) <= 1)

    def add_daily_coverage_constraint(self):
        """Assign two employees on Tuesdays/Fridays and one on other days."""

        year = self.schedule_month.year
        month = self.schedule_month.month

        days = monthrange(year, month)[1]

        for day in range(1, days + 1):

            current_date = date(year, month, day)

            vars = [
                self.variables[(user.id, current_date, shift.id)]
                for user in self.users
                for shift in self.shifts_for_date(current_date)
            ]
            required_people = 1
            if (
                not self.uses_nurse_rules
                and not self.is_weekend_rule_date(current_date)
                and current_date.weekday() in self.schedule_month.increased_staff_weekday_set
            ):
                required_people = self.schedule_month.increased_staff_count
            remaining_people = max(
                required_people - self.manual_count_by_date.get(current_date, 0),
                0,
            )
            self.model.Add(sum(vars) == remaining_people)
            if required_people > 1:
                part_time_vars = [
                    self.variables[(user.id, current_date, shift.id)]
                    for user in self.users
                    if user.employee_type == User.EmployeeType.PART_TIME
                    for shift in self.shifts_for_date(current_date)
                ]
                remaining_part_time_places = max(
                    1 - self.manual_part_time_count_by_date.get(current_date, 0),
                    0,
                )
                self.model.Add(sum(part_time_vars) <= remaining_part_time_places)

    def add_no_consecutive_weekend_days_constraint(self):
        """A person may not be assigned on both Saturday and Sunday."""
        year = self.schedule_month.year
        month = self.schedule_month.month
        days = monthrange(year, month)[1]

        for day in range(1, days):
            current_date = date(year, month, day)
            next_date = current_date + timedelta(days=1)
            if current_date.weekday() != 5 or next_date.weekday() != 6:
                continue

            for user in self.users:
                saturday_assignments = [
                    self.variables[(user.id, current_date, shift.id)]
                    for shift in self.shifts_for_date(current_date)
                ]
                sunday_assignments = [
                    self.variables[(user.id, next_date, shift.id)]
                    for shift in self.shifts_for_date(next_date)
                ]
                manual_assignments = sum(
                    (user.id, duty_date) in self.manual_duty_dates
                    for duty_date in (current_date, next_date)
                )
                self.model.Add(
                    sum(saturday_assignments + sunday_assignments)
                    <= max(1 - manual_assignments, 0)
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

                for shift in self.shifts_for_date(current_date):
                    self.model.Add(
                        self.variables[(user.id, current_date, shift.id)] == 0
                    )

    def add_absences_constraint(self):
        """An approved absence is a hard ban on all shifts for that date."""
        year = self.schedule_month.year
        month = self.schedule_month.month
        days = monthrange(year, month)[1]

        for day in range(1, days + 1):
            current_date = date(year, month, day)
            for user in self.users:
                if (user.id, current_date) not in self.absences:
                    continue
                for shift in self.shifts_for_date(current_date):
                    self.model.Add(
                        self.variables[(user.id, current_date, shift.id)] == 0
                    )

    def add_part_time_days_constraint(self):
        """Part-time employees may work only on Tuesdays and Fridays."""
        if self.uses_nurse_rules:
            return
        year = self.schedule_month.year
        month = self.schedule_month.month
        days = monthrange(year, month)[1]

        for user in self.users:
            if user.employee_type != User.EmployeeType.PART_TIME:
                continue
            for day in range(1, days + 1):
                current_date = date(year, month, day)
                if (
                    not self.is_weekend_rule_date(current_date)
                    and current_date.weekday() in self.schedule_month.part_time_allowed_weekday_set
                ):
                    continue
                for shift in self.shifts_for_date(current_date):
                    self.model.Add(
                        self.variables[(user.id, current_date, shift.id)] == 0
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

                for shift in self.shifts_for_date(current_date):
                    vars.append(self.variables[(user.id, current_date, shift.id)])
                    coeffs.append(shift.hour_units_for_date(current_date))

            self.model.Add(
                sum(
                    v * h
                    for v, h in zip(vars, coeffs)
                ) <= max(
                    self.target_hour_units[user.id] - self.manual_hour_units.get(user.id, 0),
                    0,
                )
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

                for shift in self.shifts_for_date(current_date):
                    hours.append(
                        self.variables[(user.id, current_date, shift.id)]
                        * shift.hour_units_for_date(current_date)
                    )


            self.model.Add(
                self.worked_hour_units[user.id]
                ==
                self.manual_hour_units.get(user.id, 0) + sum(hours)
            )    

    def consecutive_assignment_score(self):
        """Return a score counting adjacent calendar-day assignments."""
        year = self.schedule_month.year
        month = self.schedule_month.month
        days = monthrange(year, month)[1]
        consecutive_assignments = []

        for user in self.users:
            for day in range(1, days):
                current_date = date(year, month, day)
                next_date = current_date + timedelta(days=1)
                works_current_day = self.model.NewBoolVar(
                    f"works_user_{user.id}_date_{day}"
                )
                works_next_day = self.model.NewBoolVar(
                    f"works_user_{user.id}_date_{day + 1}"
                )
                self.model.Add(
                    works_current_day
                    == int((user.id, current_date) in self.manual_duty_dates) + sum(
                        self.variables[(user.id, current_date, shift.id)]
                        for shift in self.shifts_for_date(current_date)
                    )
                )
                self.model.Add(
                    works_next_day
                    == int((user.id, next_date) in self.manual_duty_dates) + sum(
                        self.variables[(user.id, next_date, shift.id)]
                        for shift in self.shifts_for_date(next_date)
                    )
                )

                is_consecutive = self.model.NewBoolVar(
                    f"consecutive_user_{user.id}_date_{day}"
                )
                self.model.AddMultiplicationEquality(
                    is_consecutive,
                    [works_current_day, works_next_day],
                )
                consecutive_assignments.append(is_consecutive)

        return sum(consecutive_assignments)

    def part_time_mixed_team_score(self):
        """Prefer one part-time employee on increased-staff days when feasible."""
        return sum(
            var
            for (user_id, duty_date, _shift_id), var in self.variables.items()
            if self.users_map[user_id].employee_type == User.EmployeeType.PART_TIME
            and not self.is_weekend_rule_date(duty_date)
            and duty_date.weekday() in self.schedule_month.increased_staff_weekday_set
        )

    def solve(self):
        # Priorities: explicit availability, no adjacent shifts, then balanced hours.
        preferred_assignments = []
        for (user_id, duty_date, shift_id), var in self.variables.items():
            if self.preferences.get((user_id, duty_date)) == DutyDatePreference.Status.AVAILABLE:
                preferred_assignments.append(var)

        preference_score = sum(preferred_assignments)
        self.model.Maximize(preference_score)
        status = self.solver.Solve(self.model)

        if status not in (
            cp_model.OPTIMAL,
            cp_model.FEASIBLE,
        ):
            raise ScheduleGenerationError(
                "Невозможно построить график. Проверьте нормы часов, "
                "недоступные даты и количество сотрудников."
            )

        best_preference_score = self.solver.Value(preference_score)
        self.model.Add(preference_score == best_preference_score)

        if self.uses_nurse_rules:
            consecutive_score = self.consecutive_assignment_score()
            maximum_upper = max(self.worked_hour_units_upper.values())
            maximum_hours = self.model.NewIntVar(
                0,
                maximum_upper,
                "nurse_maximum_hours",
            )
            minimum_hours = self.model.NewIntVar(
                0,
                maximum_upper,
                "nurse_minimum_hours",
            )
            self.model.AddMaxEquality(maximum_hours, list(self.worked_hour_units.values()))
            self.model.AddMinEquality(minimum_hours, list(self.worked_hour_units.values()))
            # One avoided adjacent duty matters more than the remaining hours spread.
            self.model.Minimize(
                consecutive_score * (maximum_upper + 1)
                + maximum_hours
                - minimum_hours
            )
            status = self.solver.Solve(self.model)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                raise ScheduleGenerationError(
                    "Невозможно равномерно распределить суточные смены медсестёр."
                )
        else:
            part_time_score = self.part_time_mixed_team_score()
            self.model.Maximize(part_time_score)
            status = self.solver.Solve(self.model)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                raise ScheduleGenerationError("Невозможно подобрать смешанный состав смен.")

            best_part_time_score = self.solver.Value(part_time_score)
            self.model.Add(part_time_score == best_part_time_score)
            consecutive_score = self.consecutive_assignment_score()
            self.model.Minimize(consecutive_score)
            status = self.solver.Solve(self.model)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                raise ScheduleGenerationError("Невозможно уменьшить количество смен подряд.")

            best_consecutive_score = self.solver.Value(consecutive_score)
            self.model.Add(consecutive_score == best_consecutive_score)

            squared_hours = []
            for user in self.users:
                maximum = self.worked_hour_units_upper[user.id]
                square = self.model.NewIntVar(
                    0,
                    maximum * maximum,
                    f"hours_square_{user.id}",
                )
                self.model.AddMultiplicationEquality(
                    square,
                    [self.worked_hour_units[user.id], self.worked_hour_units[user.id]],
                )
                squared_hours.append(square)

            self.model.Minimize(sum(squared_hours))
            status = self.solver.Solve(self.model)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                raise ScheduleGenerationError("Невозможно равномерно распределить смены.")


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

    def save(self):
        with transaction.atomic():
            Duty.objects.filter(
                team=self.schedule_month.team,
                date__year=self.schedule_month.year,
                date__month=self.schedule_month.month,
                generated=True,
            ).delete()

            Duty.objects.bulk_create(
                [
                    Duty(
                        team=self.schedule_month.team,
                        user=duty["user"],
                        date=duty["date"],
                        shift_type=duty["shift_type"],
                        generated=True,
                    )
                    for duty in self.generated_duties
                ]
            )
    def generate(self):

        self.load_data()

        self.create_variables()

        self.add_one_shift_per_day_constraint()

        self.add_daily_coverage_constraint()

        self.add_no_consecutive_weekend_days_constraint()

        self.add_preferences_constraint()

        self.add_absences_constraint()

        self.add_part_time_days_constraint()

        self.add_hours_constraint()

        self.add_worked_hours_constraint()

        self.solve()

        self.save()
