from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

from django.test import TestCase
from django.contrib.auth import authenticate
from django.urls import reverse
from django.utils import timezone

from .models import (
    Duty,
    DutyDatePreference,
    EmployeeAbsence,
    PartTimeWorkload,
    PreferenceActivity,
    ScheduleMonth,
    ScheduleHoliday,
    ShiftType,
    Team,
    TeamMembership,
    User,
)
from .services.schedule_generator import ScheduleGenerationError, ScheduleGenerator


class ScheduleGeneratorTests(TestCase):
    def setUp(self):
        self.team = Team.objects.create(name="Тестовый коллектив")
        self.schedule_month = ScheduleMonth.objects.create(
            team=self.team,
            year=2026,
            month=1,
            main_employee_hours=300,
        )
        self.weekday_shift = ShiftType.objects.create(
            team=self.team,
            name="Будняя смена",
            hours=11,
            day_type=ShiftType.DayType.WEEKDAY,
        )
        self.weekend_shift = ShiftType.objects.create(
            team=self.team,
            name="Суточная смена",
            hours=23,
            day_type=ShiftType.DayType.WEEKEND,
        )
        self.users = [
            User.objects.create_user(
                username=f"employee-{number}",
                password="test-password",
                full_name=f"Сотрудник {number}",
            )
            for number in range(1, 4)
        ]
        TeamMembership.objects.bulk_create(
            [
                TeamMembership(team=self.team, user=user)
                for user in self.users
            ]
        )
        self.client.force_login(self.users[0])

    def make_manager(self, username, full_name):
        user = User.objects.create_user(
            username=username,
            password="test-password",
            full_name=full_name,
            is_staff=True,
        )
        TeamMembership.objects.create(
            team=self.team,
            user=user,
            role=TeamMembership.Role.MANAGER,
            participates_in_schedule=False,
        )
        return user

    def test_generates_two_shifts_on_tuesday_and_friday(self):
        ScheduleGenerator(self.schedule_month).generate()

        duties = Duty.objects.filter(date__year=2026, date__month=1)
        expected_total = sum(
            2 if date(2026, 1, day).weekday() in (1, 4) else 1
            for day in range(1, 32)
        )
        self.assertEqual(duties.count(), expected_total)

        for day in range(1, 32):
            current_date = date(2026, 1, day)
            day_duties = duties.filter(date=current_date)
            expected_people = 2 if current_date.weekday() in (1, 4) else 1
            self.assertEqual(day_duties.count(), expected_people)
            expected_shift = (
                self.weekend_shift
                if current_date.weekday() >= 5
                else self.weekday_shift
            )
            self.assertTrue(
                all(duty.shift_type == expected_shift for duty in day_duties)
            )

    def test_shift_excluded_from_generation_is_available_only_for_manual_duties(self):
        short_shift = ShiftType.objects.create(
            team=self.team,
            name="Короткая смена",
            hours=6,
            day_type=ShiftType.DayType.WEEKDAY,
            use_in_generation=False,
        )
        manual_duty = Duty.objects.create(
            team=self.team,
            user=self.users[0],
            date=date(2026, 1, 5),
            shift_type=short_shift,
            generated=False,
        )

        ScheduleGenerator(self.schedule_month).generate()

        manual_duty.refresh_from_db()
        self.assertFalse(manual_duty.generated)
        self.assertFalse(
            Duty.objects.filter(shift_type=short_shift, generated=True).exists()
        )
        self.assertEqual(
            Duty.objects.filter(team=self.team, date=manual_duty.date).count(),
            1,
        )

    def test_nurse_team_gets_one_23_hour_shift_every_day(self):
        nurse_team = Team.objects.create(
            name="Медсёстры",
            schedule_rules=Team.ScheduleRules.NURSES,
        )
        nurse_month = ScheduleMonth.objects.create(
            team=nurse_team,
            year=2026,
            month=1,
            main_employee_hours=300,
        )
        nurse_shift = ShiftType.objects.create(
            team=nurse_team,
            name="Суточная смена",
            hours=23,
            day_type=ShiftType.DayType.WEEKDAY,
        )
        nurses = [
            User.objects.create_user(
                username=f"nurse-{number}",
                password="test-password",
                full_name=f"Медсестра {number}",
            )
            for number in range(1, 5)
        ]
        TeamMembership.objects.bulk_create(
            [TeamMembership(team=nurse_team, user=nurse) for nurse in nurses]
        )

        ScheduleGenerator(nurse_month).generate()

        duties = Duty.objects.filter(team=nurse_team).select_related("shift_type")
        self.assertEqual(duties.count(), 31)
        for day in range(1, 32):
            duty = duties.get(date=date(2026, 1, day))
            self.assertEqual(duty.shift_type, nurse_shift)
            self.assertEqual(duty.shift_type.hours, 23)

    def test_nurse_team_rejects_non_23_hour_shift_configuration(self):
        nurse_team = Team.objects.create(
            name="Медсёстры без суточной будней",
            schedule_rules=Team.ScheduleRules.NURSES,
        )
        nurse_month = ScheduleMonth.objects.create(
            team=nurse_team,
            year=2026,
            month=1,
            main_employee_hours=300,
        )
        ShiftType.objects.create(
            team=nurse_team,
            name="Короткая будняя",
            hours=11,
            day_type=ShiftType.DayType.WEEKDAY,
        )
        with self.assertRaisesMessage(
            ScheduleGenerationError,
            "создайте хотя бы одну 23-часовую смену",
        ):
            ScheduleGenerator(nurse_month).load_data()

    def test_respects_unavailable_date(self):
        unavailable_day = date(2026, 1, 5)
        DutyDatePreference.objects.create(
            user=self.users[0],
            month=self.schedule_month,
            date=unavailable_day,
            status=DutyDatePreference.Status.UNAVAILABLE,
        )

        ScheduleGenerator(self.schedule_month).generate()

        self.assertFalse(
            Duty.objects.filter(user=self.users[0], date=unavailable_day).exists()
        )

    def test_repeated_generation_preserves_and_accounts_for_manual_duty(self):
        manual_date = date(2026, 1, 5)
        manual_duty = Duty.objects.create(
            team=self.team,
            user=self.users[0],
            date=manual_date,
            shift_type=self.weekday_shift,
            generated=False,
        )

        ScheduleGenerator(self.schedule_month).generate()
        ScheduleGenerator(self.schedule_month).generate()

        manual_duty.refresh_from_db()
        self.assertFalse(manual_duty.generated)
        self.assertEqual(
            Duty.objects.filter(team=self.team, date=manual_date).count(),
            1,
        )
        self.assertLessEqual(
            sum(
                duty.hours
                for duty in Duty.objects.filter(user=self.users[0]).select_related("shift_type")
            ),
            self.schedule_month.main_employee_hours,
        )

    def test_weekday_holiday_uses_weekend_shift_rules(self):
        holiday_date = date(2026, 1, 6)
        ScheduleHoliday.objects.create(
            month=self.schedule_month,
            date=holiday_date,
        )

        ScheduleGenerator(self.schedule_month).generate()

        holiday_duties = Duty.objects.filter(date=holiday_date)
        self.assertEqual(holiday_duties.count(), 1)
        self.assertTrue(
            all(duty.shift_type == self.weekend_shift for duty in holiday_duties)
        )

        response = self.client.get(
            reverse("month_schedule"),
            {"team": self.team.id, "year": 2026, "month": 1},
        )
        self.assertContains(response, "Праздник (как выходной)")
        self.assertContains(response, "holiday-cell")

    def test_overnight_shift_on_last_day_counts_only_hours_inside_month(self):
        generator = ScheduleGenerator(self.schedule_month)

        generator.generate()

        last_day_duty = Duty.objects.select_related("shift_type").get(
            date=date(2026, 1, 31)
        )
        self.assertEqual(last_day_duty.shift_type, self.weekend_shift)
        self.assertEqual(last_day_duty.hours, Decimal("14.5"))
        regular_overnight_duty = Duty.objects.select_related("shift_type").filter(
            shift_type=self.weekend_shift,
            date__lt=date(2026, 1, 31),
        ).first()
        self.assertEqual(regular_overnight_duty.hours, Decimal("23"))

        for user in self.users:
            expected_units = sum(
                duty.hour_units
                for duty in Duty.objects.filter(user=user).select_related("shift_type")
            )
            self.assertEqual(
                generator.solver.Value(generator.worked_hour_units[user.id]),
                expected_units,
            )

    def test_regeneration_replaces_previous_automatic_schedule(self):
        ScheduleGenerator(self.schedule_month).generate()
        ScheduleGenerator(self.schedule_month).generate()

        self.assertEqual(
            Duty.objects.filter(date__year=2026, date__month=1, generated=True).count(),
            40,
        )

    def test_part_time_employee_is_limited_to_half_main_hours(self):
        part_time_user = self.users[0]
        part_time_user.employee_type = User.EmployeeType.PART_TIME
        part_time_user.save(update_fields=["employee_type"])
        PartTimeWorkload.objects.create(
            month=self.schedule_month,
            user=part_time_user,
            hours=250,
        )

        generator = ScheduleGenerator(self.schedule_month)
        generator.load_data()

        self.assertEqual(generator.target_hours[part_time_user.id], 150)

    def test_part_time_employee_works_only_on_tuesday_or_friday(self):
        part_time_user = self.users[0]
        part_time_user.employee_type = User.EmployeeType.PART_TIME
        part_time_user.save(update_fields=["employee_type"])

        ScheduleGenerator(self.schedule_month).generate()

        assigned_dates = list(
            Duty.objects.filter(user=part_time_user).values_list("date", flat=True)
        )
        self.assertTrue(assigned_dates)
        self.assertTrue(
            all(assigned_date.weekday() in (1, 4) for assigned_date in assigned_dates)
        )

    def test_increased_days_prefer_one_main_and_one_part_time_employee(self):
        self.users[0].employee_type = User.EmployeeType.PART_TIME
        self.users[0].save(update_fields=["employee_type"])
        self.users[1].employee_type = User.EmployeeType.PART_TIME
        self.users[1].save(update_fields=["employee_type"])
        extra_main = User.objects.create_user(
            username="extra-main",
            password="test-password",
            full_name="Дополнительный основной",
        )
        TeamMembership.objects.create(team=self.team, user=extra_main)

        ScheduleGenerator(self.schedule_month).generate()

        duties = Duty.objects.filter(date__year=2026, date__month=1)
        mixed_days = 0
        for day in range(1, 32):
            current_date = date(2026, 1, day)
            if current_date.weekday() not in (1, 4):
                continue
            day_users = User.objects.filter(duty__in=duties.filter(date=current_date))
            part_time_count = day_users.filter(
                employee_type=User.EmployeeType.PART_TIME
            ).count()
            self.assertLessEqual(part_time_count, 1)
            if part_time_count == 1:
                mixed_days += 1
                self.assertEqual(
                    day_users.filter(employee_type=User.EmployeeType.MAIN).count(),
                    1,
                )
        self.assertGreater(mixed_days, 0)

    def test_anonymous_user_cannot_open_schedule_or_export(self):
        self.client.logout()

        schedule_response = self.client.get(reverse("month_schedule"))
        export_response = self.client.get(reverse("export_month_schedule"))

        self.assertRedirects(
            schedule_response,
            f"{reverse('login')}?next={reverse('month_schedule')}",
        )
        self.assertEqual(export_response.status_code, 302)

    def test_month_staffing_weekdays_are_configurable(self):
        self.schedule_month.increased_staff_weekdays = "2"
        self.schedule_month.increased_staff_count = 2
        self.schedule_month.save(
            update_fields=["increased_staff_weekdays", "increased_staff_count"]
        )

        ScheduleGenerator(self.schedule_month).generate()

        duties = Duty.objects.filter(date__year=2026, date__month=1)
        for day in range(1, 32):
            current_date = date(2026, 1, day)
            expected_people = 2 if current_date.weekday() == 2 else 1
            self.assertEqual(duties.filter(date=current_date).count(), expected_people)

    def test_part_time_days_and_percent_are_configurable(self):
        self.schedule_month.part_time_allowed_weekdays = "0"
        self.schedule_month.part_time_hours_percent = 40
        self.schedule_month.save(
            update_fields=["part_time_allowed_weekdays", "part_time_hours_percent"]
        )
        part_time_user = self.users[0]
        part_time_user.employee_type = User.EmployeeType.PART_TIME
        part_time_user.save(update_fields=["employee_type"])

        generator = ScheduleGenerator(self.schedule_month)
        generator.generate()

        assigned_dates = list(
            Duty.objects.filter(user=part_time_user).values_list("date", flat=True)
        )
        self.assertEqual(generator.target_hours[part_time_user.id], 120)
        self.assertTrue(assigned_dates)
        self.assertTrue(all(assigned_date.weekday() == 0 for assigned_date in assigned_dates))

    def test_does_not_assign_employee_on_both_weekend_days(self):
        ScheduleGenerator(self.schedule_month).generate()

        duties = Duty.objects.filter(date__year=2026, date__month=1)
        for saturday_day in (3, 10, 17, 24, 31):
            saturday = date(2026, 1, saturday_day)
            sunday = saturday + timedelta(days=1)
            if sunday.month != saturday.month:
                continue

            saturday_user_ids = set(
                duties.filter(date=saturday).values_list("user_id", flat=True)
            )
            sunday_user_ids = set(
                duties.filter(date=sunday).values_list("user_id", flat=True)
            )
            self.assertSetEqual(saturday_user_ids & sunday_user_ids, set())

    def test_avoids_adjacent_day_assignments_when_possible(self):
        ScheduleGenerator(self.schedule_month).generate()

        for user in self.users:
            assigned_dates = list(
                Duty.objects.filter(user=user)
                .order_by("date")
                .values_list("date", flat=True)
            )
            for current_date, next_date in zip(assigned_dates, assigned_dates[1:]):
                self.assertGreater((next_date - current_date).days, 1)

    def test_schedule_shows_hours_and_weekends(self):
        ScheduleGenerator(self.schedule_month).generate()

        response = self.client.get(
            f"{reverse('month_schedule')}?year=2026&month=1"
        )

        self.assertContains(response, "Часы")
        self.assertContains(response, "weekend-cell")
        self.assertContains(response, 'name="month"')
        self.assertContains(response, 'name="year"')
        self.assertContains(response, "Сегодня")
        self.assertContains(response, "schedule-table-wrap")
        self.assertContains(response, 'name="viewport"')
        self.assertContains(response, "осн")

    def test_schedule_opens_on_current_month_by_default(self):
        response = self.client.get(reverse("month_schedule"))
        today = timezone.now().date()

        self.assertEqual(response.context["year"], today.year)
        self.assertEqual(response.context["month"], today.month)

    def test_schedule_colors_employee_preferences(self):
        DutyDatePreference.objects.create(
            user=self.users[0],
            month=self.schedule_month,
            date=date(2026, 1, 5),
            status=DutyDatePreference.Status.AVAILABLE,
        )
        DutyDatePreference.objects.create(
            user=self.users[0],
            month=self.schedule_month,
            date=date(2026, 1, 6),
            status=DutyDatePreference.Status.UNAVAILABLE,
        )

        response = self.client.get(
            f"{reverse('month_schedule')}?year=2026&month=1"
        )

        self.assertContains(response, "preference-available")
        self.assertContains(response, "preference-unavailable")
        self.assertContains(response, "Могу")
        self.assertContains(response, "Не могу")

    def test_schedule_marks_part_time_employee(self):
        self.users[0].employee_type = User.EmployeeType.PART_TIME
        self.users[0].save(update_fields=["employee_type"])

        response = self.client.get(
            f"{reverse('month_schedule')}?period=2026-01"
        )

        self.assertContains(response, "совм")

    def test_exports_selected_month_to_excel(self):
        self.users[0].employee_type = User.EmployeeType.PART_TIME
        self.users[0].save(update_fields=["employee_type"])
        ScheduleGenerator(self.schedule_month).generate()

        response = self.client.get(
            f"{reverse('export_month_schedule')}?year=2026&month=1"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("schedule-2026-01.xlsx", response["Content-Disposition"])
        with ZipFile(BytesIO(response.content)) as workbook:
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("Сотрудник", sheet)
        self.assertIn("Январь", sheet)
        self.assertIn("<t>Sum</t>", sheet)

    def test_employee_can_save_preference_from_calendar(self):
        self.client.force_login(self.users[0])

        calendar_response = self.client.get(
            reverse("preference_calendar", args=[self.schedule_month.id])
        )
        self.assertContains(calendar_response, "Могу")
        self.assertContains(calendar_response, "Не могу")

        response = self.client.post(
            reverse("set_preference", args=[self.schedule_month.id]),
            {"date": "2026-01-05", "status": DutyDatePreference.Status.UNAVAILABLE},
        )

        self.assertRedirects(
            response,
            reverse("preference_calendar", args=[self.schedule_month.id]),
        )
        self.assertEqual(
            DutyDatePreference.objects.get(
                user=self.users[0],
                date=date(2026, 1, 5),
            ).status,
            DutyDatePreference.Status.UNAVAILABLE,
        )
        self.assertTrue(
            PreferenceActivity.objects.filter(
                user=self.users[0],
                month=self.schedule_month,
            ).exists()
        )

    def test_employee_can_save_multiple_preferences_without_page_reload(self):
        url = reverse("set_preference", args=[self.schedule_month.id])
        for day in (7, 8, 9):
            response = self.client.post(
                url,
                {
                    "date": f"2026-01-{day:02d}",
                    "status": DutyDatePreference.Status.UNAVAILABLE,
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "UNAVAILABLE")

        self.assertEqual(
            DutyDatePreference.objects.filter(
                user=self.users[0],
                status=DutyDatePreference.Status.UNAVAILABLE,
            ).count(),
            3,
        )

    def test_employee_can_sign_in_by_username_or_full_name(self):
        employee = self.users[0]

        self.assertEqual(
            authenticate(username=employee.username, password="test-password"),
            employee,
        )
        self.assertEqual(
            authenticate(username=employee.full_name, password="test-password"),
            employee,
        )

    def test_staff_can_edit_another_employees_preferences(self):
        admin_user = self.make_manager(
            "administrator",
            "Администратор",
        )
        self.client.force_login(admin_user)

        calendar_response = self.client.get(
            reverse(
                "admin_preference_calendar",
                args=[self.schedule_month.id, self.users[0].id],
            )
        )
        self.assertContains(calendar_response, "Самостоятельно отметились")

        response = self.client.post(
            reverse(
                "admin_set_preference",
                args=[self.schedule_month.id, self.users[0].id],
            ),
            {"date": "2026-01-06", "status": DutyDatePreference.Status.AVAILABLE},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            DutyDatePreference.objects.filter(
                user=self.users[0],
                date=date(2026, 1, 6),
                status=DutyDatePreference.Status.AVAILABLE,
            ).exists()
        )

    def test_employee_cannot_edit_another_employee(self):
        self.client.force_login(self.users[0])

        response = self.client.get(
            reverse(
                "admin_preference_calendar",
                args=[self.schedule_month.id, self.users[1].id],
            )
        )

        self.assertEqual(response.status_code, 403)

    def test_only_staff_can_open_schedule_regeneration(self):
        self.client.force_login(self.users[0])

        response = self.client.get(
            reverse("generate_schedule", args=[self.schedule_month.id])
        )

        self.assertEqual(response.status_code, 403)

    def test_staff_sees_month_and_shift_setup_actions(self):
        admin_user = self.make_manager(
            "schedule-admin",
            "Администратор графика",
        )
        self.client.force_login(admin_user)

        configured_response = self.client.get(
            f"{reverse('month_schedule')}?year=2026&month=1"
        )
        self.assertContains(configured_response, "Создать график")
        self.assertContains(configured_response, "Типы смен")

        empty_response = self.client.get(
            f"{reverse('month_schedule')}?year=2026&month=2"
        )
        self.assertContains(empty_response, "Настроить этот месяц")

    def test_generation_page_lists_configured_shifts(self):
        admin_user = self.make_manager(
            "generation-admin",
            "Администратор генерации",
        )
        self.client.force_login(admin_user)

        response = self.client.get(
            reverse("generate_schedule", args=[self.schedule_month.id])
        )

        self.assertContains(response, "Будняя смена")
        self.assertContains(response, "Суточная смена")
        self.assertContains(response, "Изменить смены")

    def test_separate_admin_is_not_treated_as_employee(self):
        admin_user = User.objects.create_superuser(
            username="separate-admin",
            password="test-password",
            full_name="Администратор",
        )

        generator = ScheduleGenerator(self.schedule_month)
        generator.load_data()
        self.assertNotIn(admin_user, generator.users)

        self.client.force_login(admin_user)
        schedule_response = self.client.get(
            f"{reverse('month_schedule')}?team={self.team.id}&year=2026&month=1"
        )
        self.assertNotIn(
            admin_user.id,
            [row["user"].id for row in schedule_response.context["rows"]],
        )
        self.assertTrue(schedule_response.context["can_manage"])
        self.assertFalse(schedule_response.context["can_participate"])
        self.assertIsNotNone(schedule_response.context["first_employee"])
        self.assertContains(schedule_response, "Предпочтения сотрудников")

        preferences_response = self.client.get(
            reverse("preference_calendar", args=[self.schedule_month.id])
        )
        self.assertRedirects(
            preferences_response,
            reverse(
                "admin_preference_calendar",
                args=[self.schedule_month.id, self.users[0].id],
            ),
        )

    def test_manager_cannot_access_another_team(self):
        other_team = Team.objects.create(name="Другой коллектив")
        other_month = ScheduleMonth.objects.create(
            team=other_team,
            year=2026,
            month=1,
            main_employee_hours=300,
        )
        manager = self.make_manager("manager", "Заведующий")
        self.client.force_login(manager)

        schedule_response = self.client.get(
            f"{reverse('month_schedule')}?team={other_team.id}&year=2026&month=1"
        )
        generate_response = self.client.get(
            reverse("generate_schedule", args=[other_month.id])
        )

        self.assertEqual(schedule_response.status_code, 404)
        self.assertEqual(generate_response.status_code, 403)

    def test_teams_have_isolated_employees_and_shift_types(self):
        other_team = Team.objects.create(name="Второй коллектив")
        other_month = ScheduleMonth.objects.create(
            team=other_team,
            year=2026,
            month=1,
            main_employee_hours=300,
        )
        other_user = User.objects.create_user(
            username="other-employee",
            password="test-password",
            full_name="Сотрудник другого коллектива",
        )
        TeamMembership.objects.create(team=other_team, user=other_user)
        other_weekday_shift = ShiftType.objects.create(
            team=other_team,
            name="Другая будняя смена",
            hours=11,
            day_type=ShiftType.DayType.WEEKDAY,
        )
        other_weekend_shift = ShiftType.objects.create(
            team=other_team,
            name="Другая выходная смена",
            hours=23,
            day_type=ShiftType.DayType.WEEKEND,
        )

        generator = ScheduleGenerator(other_month)
        generator.load_data()

        self.assertEqual(generator.users, [other_user])
        self.assertEqual(generator.weekday_shifts, [other_weekday_shift])
        self.assertEqual(generator.weekend_shifts, [other_weekend_shift])

    def test_manager_admin_lists_only_own_team_data(self):
        other_team = Team.objects.create(name="Закрытый коллектив")
        ScheduleMonth.objects.create(
            team=other_team,
            year=2027,
            month=2,
            main_employee_hours=150,
        )
        manager = self.make_manager("admin-manager", "Заведующий коллективом")
        self.client.force_login(manager)

        response = self.client.get(
            reverse("admin:work_schedule_schedulemonth_changelist")
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.team.name)
        self.assertNotContains(response, other_team.name)
        self.assertContains(response, "Месяцы графика")
        self.assertContains(response, "Коллектив")
        self.assertContains(response, "Год")
        self.assertContains(response, "Месяц")
        self.assertContains(response, "Норма часов основного сотрудника")

    def test_manager_can_configure_holidays_inside_schedule_month(self):
        manager = self.make_manager("holiday-manager", "Заведующий праздниками")
        self.client.force_login(manager)

        response = self.client.get(
            reverse(
                "admin:work_schedule_schedulemonth_change",
                args=[self.schedule_month.id],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Праздничные дни")
        self.assertContains(response, "holidays-0-date")

    def test_manager_can_edit_assignment_and_make_it_manual(self):
        ScheduleGenerator(self.schedule_month).generate()
        duty = Duty.objects.filter(generated=True).first()
        manager = self.make_manager("duty-manager", "Заведующий сменами")
        self.client.force_login(manager)

        response = self.client.post(
            reverse("admin:work_schedule_duty_change", args=[duty.id]),
            {
                "team": self.team.id,
                "user": duty.user_id,
                "date": duty.date.isoformat(),
                "shift_type": duty.shift_type_id,
                "_save": "Сохранить",
            },
        )

        self.assertRedirects(response, reverse("admin:work_schedule_duty_changelist"))
        duty.refresh_from_db()
        self.assertFalse(duty.generated)

        month_response = self.client.get(
            reverse("month_schedule"),
            {"team": self.team.id, "year": 2026, "month": 1},
        )
        self.assertContains(month_response, "Редактировать назначения")
        self.assertContains(month_response, "Смена задана вручную")
        self.assertContains(month_response, "manual-duty-cell")
        self.assertContains(
            month_response,
            reverse("admin:work_schedule_duty_change", args=[duty.id]),
        )
        self.assertContains(month_response, reverse("admin:work_schedule_duty_add"))

    def test_employee_registers_only_in_invited_team(self):
        other_team = Team.objects.create(name="Коллектив по приглашению")
        registration_url = reverse(
            "register_employee",
            args=[other_team.registration_token],
        )

        response = self.client.post(
            registration_url,
            {
                "username": "invited-employee",
                "full_name": "Новый сотрудник",
                "password1": "Reliable-local-password-2026",
                "password2": "Reliable-local-password-2026",
            },
        )

        user = User.objects.get(username="invited-employee")
        self.assertRedirects(
            response,
            f"{reverse('month_schedule')}?team={other_team.id}",
        )
        self.assertTrue(
            TeamMembership.objects.filter(
                user=user,
                team=other_team,
                role=TeamMembership.Role.EMPLOYEE,
                participates_in_schedule=True,
            ).exists()
        )
        self.assertEqual(user.team_memberships.count(), 1)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.id)

    def test_manager_can_view_qr_and_replace_invitation(self):
        manager = self.make_manager("invite-manager", "Заведующий регистрацией")
        self.client.force_login(manager)
        old_token = self.team.registration_token

        page_response = self.client.get(
            reverse("team_invitation", args=[self.team.id])
        )
        qr_response = self.client.get(
            reverse("team_invitation_qr", args=[self.team.id])
        )
        reset_response = self.client.post(
            reverse("reset_team_invitation", args=[self.team.id])
        )

        self.assertContains(page_response, str(old_token))
        self.assertEqual(qr_response.status_code, 200)
        self.assertEqual(qr_response["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", qr_response.content)
        self.assertRedirects(
            reset_response,
            reverse("team_invitation", args=[self.team.id]),
        )
        self.team.refresh_from_db()
        self.assertNotEqual(self.team.registration_token, old_token)
        self.assertEqual(
            self.client.get(reverse("register_employee", args=[old_token])).status_code,
            404,
        )

    def test_manager_cannot_open_another_team_invitation(self):
        other_team = Team.objects.create(name="Чужое приглашение")
        manager = self.make_manager("limited-manager", "Ограниченный заведующий")
        self.client.force_login(manager)

        page_response = self.client.get(
            reverse("team_invitation", args=[other_team.id])
        )
        qr_response = self.client.get(
            reverse("team_invitation_qr", args=[other_team.id])
        )

        self.assertEqual(page_response.status_code, 403)
        self.assertEqual(qr_response.status_code, 403)

    def test_manager_can_add_vacation_range_in_annual_calendar(self):
        manager = self.make_manager("vacation-manager", "Заведующий отпусками")
        self.client.force_login(manager)

        page_response = self.client.get(
            reverse("annual_vacations", args=[self.team.id]),
            {"year": 2026, "user": self.users[0].id},
        )
        save_response = self.client.post(
            reverse("set_vacation", args=[self.team.id, self.users[0].id]),
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-05",
                "action": "ADD",
            },
        )

        self.assertContains(page_response, "Январь")
        self.assertContains(page_response, "Декабрь")
        self.assertContains(page_response, self.users[0].full_name)
        self.assertRedirects(
            save_response,
            f"{reverse('annual_vacations', args=[self.team.id])}"
            f"?year=2026&user={self.users[0].id}",
        )
        absences = EmployeeAbsence.objects.filter(user=self.users[0])
        self.assertEqual(absences.count(), 5)
        self.assertTrue(all(absence.created_by == manager for absence in absences))

    def test_employee_can_view_but_cannot_edit_own_vacation(self):
        page_response = self.client.get(
            reverse("annual_vacations", args=[self.team.id]),
            {"year": 2026},
        )
        save_response = self.client.post(
            reverse("set_vacation", args=[self.team.id, self.users[0].id]),
            {"date": "2026-07-01", "action": "TOGGLE"},
        )

        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, self.users[0].full_name)
        self.assertNotContains(page_response, "Добавить отпуск")
        self.assertEqual(save_response.status_code, 403)
        self.assertFalse(EmployeeAbsence.objects.exists())

    def test_annual_calendar_shows_vacations_for_all_employees_in_month_rows(self):
        manager = self.make_manager("overlap-manager", "Заведующий пересечениями")
        second_employee = User.objects.create_user(
            username="second-vacation-employee",
            password="password",
            full_name="Второй сотрудник",
        )
        TeamMembership.objects.create(
            team=self.team,
            user=second_employee,
            role=TeamMembership.Role.EMPLOYEE,
        )
        shared_date = date(2026, 7, 10)
        EmployeeAbsence.objects.bulk_create([
            EmployeeAbsence(
                user=self.users[0],
                date=shared_date,
                absence_type=EmployeeAbsence.Type.VACATION,
            ),
            EmployeeAbsence(
                user=second_employee,
                date=shared_date,
                absence_type=EmployeeAbsence.Type.VACATION,
            ),
        ])
        self.client.force_login(manager)

        response = self.client.get(
            reverse("annual_vacations", args=[self.team.id]),
            {"year": 2026},
        )

        july = response.context["months"][6]
        vacation_rows = {
            row["user"].id: row["cells"][9]["is_vacation"]
            for row in july["rows"]
        }
        self.assertTrue(vacation_rows[self.users[0].id])
        self.assertTrue(vacation_rows[second_employee.id])
        self.assertContains(response, self.users[0].full_name)
        self.assertContains(response, second_employee.full_name)

    def test_vacation_is_shown_in_month_with_existing_duty_conflict(self):
        vacation_date = date(2026, 1, 5)
        EmployeeAbsence.objects.create(
            user=self.users[0],
            date=vacation_date,
            absence_type=EmployeeAbsence.Type.VACATION,
        )
        Duty.objects.create(
            team=self.team,
            user=self.users[0],
            date=vacation_date,
            shift_type=self.weekday_shift,
        )

        response = self.client.get(
            reverse("month_schedule"),
            {"team": self.team.id, "year": 2026, "month": 1},
        )

        self.assertContains(response, "ОТП")
        self.assertContains(response, "Назначена смена")
        self.assertContains(response, "Отпуска на год")

    def test_generator_never_assigns_shift_during_vacation(self):
        vacation_date = date(2026, 1, 5)
        EmployeeAbsence.objects.create(
            user=self.users[0],
            date=vacation_date,
            absence_type=EmployeeAbsence.Type.VACATION,
        )

        ScheduleGenerator(self.schedule_month).generate()

        self.assertFalse(
            Duty.objects.filter(user=self.users[0], date=vacation_date).exists()
        )

    def test_export_marks_vacation(self):
        EmployeeAbsence.objects.create(
            user=self.users[0],
            date=date(2026, 1, 5),
            absence_type=EmployeeAbsence.Type.VACATION,
        )

        response = self.client.get(
            reverse("export_month_schedule"),
            {"team": self.team.id, "year": 2026, "month": 1},
        )

        with ZipFile(BytesIO(response.content)) as workbook:
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("<t>От</t>", sheet)

    def test_export_matches_department_month_layout(self):
        Duty.objects.create(
            team=self.team,
            user=self.users[0],
            date=date(2026, 1, 1),
            shift_type=self.weekday_shift,
            generated=False,
        )
        DutyDatePreference.objects.create(
            month=self.schedule_month,
            user=self.users[0],
            date=date(2026, 1, 2),
            status=DutyDatePreference.Status.UNAVAILABLE,
        )

        response = self.client.get(
            reverse("export_month_schedule"),
            {"team": self.team.id, "year": 2026, "month": 1},
        )

        with ZipFile(BytesIO(response.content)) as workbook:
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
            styles = workbook.read("xl/styles.xml").decode("utf-8")
        self.assertIn('name="Январь"', workbook_xml)
        self.assertIn('<mergeCell ref="B2:AI2"/>', sheet)
        self.assertIn("<t>Sum</t>", sheet)
        self.assertIn("<t>Чт</t>", sheet)
        self.assertIn("<t>Пт</t>", sheet)
        self.assertIn("<t>-</t>", sheet)
        self.assertIn("SUM(C5:AG5)", sheet)
        self.assertIn('orientation="landscape"', sheet)
        self.assertIn('rgb="FF72FCE9"', styles)
        self.assertIn('rgb="FF88F94E"', styles)
        self.assertIn('rgb="FFE2D9F3"', styles)

    def test_export_keeps_half_hour_total_for_last_day_overnight_shift(self):
        Duty.objects.create(
            team=self.team,
            user=self.users[0],
            date=date(2026, 1, 31),
            shift_type=self.weekend_shift,
            generated=False,
        )

        response = self.client.get(
            reverse("export_month_schedule"),
            {"team": self.team.id, "year": 2026, "month": 1},
        )

        with ZipFile(BytesIO(response.content)) as workbook:
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("<v>14.5</v>", sheet)

    def test_manager_cannot_open_another_team_vacations(self):
        other_team = Team.objects.create(name="Чужие отпуска")
        manager = self.make_manager("own-team-manager", "Заведующий своего коллектива")
        self.client.force_login(manager)

        response = self.client.get(
            reverse("annual_vacations", args=[other_team.id])
        )

        self.assertEqual(response.status_code, 404)
