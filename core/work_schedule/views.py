# scheduler/views.py

import calendar
import uuid
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Max, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from .models import (
    Duty,
    DutyDatePreference,
    EmployeeAbsence,
    PreferenceActivity,
    ScheduleMonth,
    ScheduleHoliday,
    ShiftType,
    Team,
    TeamMembership,
    User,
)
from .permissions import (
    accessible_teams,
    can_access_team,
    can_manage_team,
    participates_in_team,
)
from .forms import EmployeeRegistrationForm
from .services.schedule_generator import ScheduleGenerator


MONTH_NAMES = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)


def register_employee(request, token):
    team = get_object_or_404(
        Team,
        registration_token=token,
        is_active=True,
    )
    form = EmployeeRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            TeamMembership.objects.create(
                team=team,
                user=user,
                role=TeamMembership.Role.EMPLOYEE,
                participates_in_schedule=True,
            )
        login(
            request,
            user,
            backend="work_schedule.backends.UsernameOrFullNameBackend",
        )
        return redirect(f"{reverse('month_schedule')}?team={team.id}")
    return render(
        request,
        "registration/register_employee.html",
        {"form": form, "team": team},
    )


@login_required
def team_invitation(request, team_id):
    team = get_object_or_404(Team, id=team_id, is_active=True)
    if not can_manage_team(request.user, team):
        raise PermissionDenied("Управлять приглашением может только заведующий коллектива.")
    registration_url = request.build_absolute_uri(
        reverse("register_employee", args=[team.registration_token])
    )
    return render(
        request,
        "work_schedule/team_invitation.html",
        {"team": team, "registration_url": registration_url},
    )


@login_required
def team_invitation_qr(request, team_id):
    team = get_object_or_404(Team, id=team_id, is_active=True)
    if not can_manage_team(request.user, team):
        raise PermissionDenied("Управлять приглашением может только заведующий коллектива.")

    import qrcode
    from qrcode.image.svg import SvgPathImage

    registration_url = request.build_absolute_uri(
        reverse("register_employee", args=[team.registration_token])
    )
    image = qrcode.make(
        registration_url,
        image_factory=SvgPathImage,
        box_size=10,
        border=4,
    )
    output = BytesIO()
    image.save(output)
    return HttpResponse(output.getvalue(), content_type="image/svg+xml")


@login_required
def reset_team_invitation(request, team_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Нужен POST-запрос.")
    team = get_object_or_404(Team, id=team_id, is_active=True)
    if not can_manage_team(request.user, team):
        raise PermissionDenied("Управлять приглашением может только заведующий коллектива.")
    team.registration_token = uuid.uuid4()
    team.save(update_fields=["registration_token"])
    messages.success(request, "Создана новая ссылка. Предыдущая ссылка больше не работает.")
    return redirect("team_invitation", team_id=team.id)


def _team_employees(team):
    return User.objects.filter(
        is_active=True,
        team_memberships__team=team,
        team_memberships__participates_in_schedule=True,
    ).distinct().order_by("full_name")


@login_required
def annual_vacations(request, team_id):
    team = get_object_or_404(
        accessible_teams(request.user),
        id=team_id,
        is_active=True,
    )
    employees = _team_employees(team)
    can_manage = can_manage_team(request.user, team)
    current_year = timezone.now().date().year
    try:
        year = int(request.GET.get("year", current_year))
        if not 2000 <= year <= 2100:
            raise ValueError
    except (TypeError, ValueError):
        year = current_year

    selected_user = None
    requested_user_id = request.GET.get("user")
    if can_manage:
        if requested_user_id:
            selected_user = get_object_or_404(employees, id=requested_user_id)
        else:
            selected_user = employees.first()
    elif employees.filter(id=request.user.id).exists():
        selected_user = request.user
    else:
        raise PermissionDenied("Нет доступа к отпускам этого коллектива.")

    employees = list(employees)
    holiday_dates = set(
        ScheduleHoliday.objects.filter(
            month__team=team,
            month__year=year,
        ).values_list("date", flat=True)
    )
    vacation_dates_by_user = {employee.id: set() for employee in employees}
    for user_id, absence_date in EmployeeAbsence.objects.filter(
        user__in=employees,
        date__year=year,
        absence_type=EmployeeAbsence.Type.VACATION,
    ).values_list("user_id", "date"):
        vacation_dates_by_user[user_id].add(absence_date)

    months = []
    for month_number, month_name in enumerate(MONTH_NAMES, 1):
        days = []
        _, days_in_month = calendar.monthrange(year, month_number)
        for day_number in range(1, days_in_month + 1):
            current_date = date(year, month_number, day_number)
            days.append({
                "number": day_number,
                "date": current_date,
                "is_holiday": current_date in holiday_dates,
                "is_weekend": current_date.weekday() >= 5 or current_date in holiday_dates,
            })

        rows = []
        for employee in employees:
            rows.append({
                "user": employee,
                "cells": [
                    {
                        "date": day["date"],
                        "is_holiday": day["is_holiday"],
                        "is_weekend": day["is_weekend"],
                        "is_vacation": day["date"] in vacation_dates_by_user[employee.id],
                    }
                    for day in days
                ],
            })
        months.append({
            "number": month_number,
            "name": month_name,
            "days": days,
            "rows": rows,
        })

    year_options = list(range(current_year - 2, current_year + 4))
    if year not in year_options:
        year_options.append(year)
        year_options.sort()

    return render(
        request,
        "work_schedule/annual_vacations.html",
        {
            "team": team,
            "employees": employees,
            "selected_user": selected_user,
            "year": year,
            "year_options": year_options,
            "months": months,
            "can_manage": can_manage,
        },
    )


@login_required
def set_vacation(request, team_id, user_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Нужен POST-запрос.")
    team = get_object_or_404(Team, id=team_id, is_active=True)
    if not can_manage_team(request.user, team):
        raise PermissionDenied("Редактировать отпуска может только заведующий коллектива.")
    selected_user = get_object_or_404(_team_employees(team), id=user_id)

    try:
        start_date = date.fromisoformat(
            request.POST.get("start_date") or request.POST["date"]
        )
        end_date = date.fromisoformat(request.POST.get("end_date") or start_date.isoformat())
    except (KeyError, TypeError, ValueError):
        return HttpResponseBadRequest("Некорректная дата отпуска.")
    if end_date < start_date or (end_date - start_date).days > 366:
        return HttpResponseBadRequest("Некорректный диапазон отпуска.")

    action = request.POST.get("action", "TOGGLE")
    dates = [
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ]

    with transaction.atomic():
        if action == "CLEAR":
            EmployeeAbsence.objects.filter(
                user=selected_user,
                date__range=(start_date, end_date),
                absence_type=EmployeeAbsence.Type.VACATION,
            ).delete()
            is_vacation = False
        elif action == "ADD":
            EmployeeAbsence.objects.bulk_create(
                [
                    EmployeeAbsence(
                        user=selected_user,
                        date=vacation_date,
                        absence_type=EmployeeAbsence.Type.VACATION,
                        created_by=request.user,
                    )
                    for vacation_date in dates
                ],
                ignore_conflicts=True,
            )
            is_vacation = True
        elif action == "TOGGLE" and len(dates) == 1:
            absence = EmployeeAbsence.objects.filter(
                user=selected_user,
                date=start_date,
                absence_type=EmployeeAbsence.Type.VACATION,
            ).first()
            if absence:
                absence.delete()
                is_vacation = False
            else:
                EmployeeAbsence.objects.create(
                    user=selected_user,
                    date=start_date,
                    absence_type=EmployeeAbsence.Type.VACATION,
                    created_by=request.user,
                )
                is_vacation = True
        else:
            return HttpResponseBadRequest("Некорректное действие.")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "ok": True,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "is_vacation": is_vacation,
        })
    return redirect(
        f"{reverse('annual_vacations', args=[team.id])}"
        f"?year={start_date.year}&user={selected_user.id}"
    )


def _selected_period(request):
    today = timezone.now().date()
    period = request.GET.get("period", "")
    try:
        if period:
            year, month = map(int, period.split("-", 1))
        else:
            year = int(request.GET.get("year", today.year))
            month = int(request.GET.get("month", today.month))
        date(year, month, 1)
    except (TypeError, ValueError):
        return today.year, today.month
    return year, month

def _selected_team(request):
    teams = accessible_teams(request.user).order_by("name")
    team_id = request.GET.get("team")
    if team_id:
        team = get_object_or_404(teams, id=team_id)
    else:
        team = teams.first()
    if team is None:
        raise PermissionDenied("Пользователь не состоит ни в одном коллективе.")
    return team, teams


def _schedule_data(year, month, team):
    days_in_month = calendar.monthrange(year, month)[1]

    users = list(_team_employees(team))

    schedule_month = ScheduleMonth.objects.filter(
        team=team,
        year=year,
        month=month,
    ).first()
    holiday_dates = set()
    if schedule_month:
        holiday_dates = set(schedule_month.holidays.values_list("date", flat=True))

    duties = (
        Duty.objects.filter(
            team=team,
            date__year=year,
            date__month=month,
        )
        .select_related("user", "shift_type")
    )
    preferences = DutyDatePreference.objects.none()
    if schedule_month:
        preferences = DutyDatePreference.objects.filter(month=schedule_month)
    preferences_map = {
        (preference.user_id, preference.date.day): preference.status
        for preference in preferences
    }
    absences_map = {
        (absence.user_id, absence.date.day): absence
        for absence in EmployeeAbsence.objects.filter(
            user__in=users,
            date__year=year,
            date__month=month,
        )
    }

    # Словарь для быстрого поиска смен
    duties_map = {
        (duty.user_id, duty.date.day): duty
        for duty in duties
    }

    days = [
        {
            "number": day,
            "is_holiday": date(year, month, day) in holiday_dates,
            "is_weekend": (
                date(year, month, day).weekday() >= 5
                or date(year, month, day) in holiday_dates
            ),
        }
        for day in range(1, days_in_month + 1)
    ]
    rows = []

    for user in users:
        cells = []

        for day_info in days:
            day = day_info["number"]
            duty = duties_map.get((user.id, day))
            cells.append(
                {
                    "duty": duty,
                    "absence": absences_map.get((user.id, day)),
                    "date": date(year, month, day),
                    "is_holiday": day_info["is_holiday"],
                    "is_weekend": day_info["is_weekend"],
                    "preference_status": preferences_map.get((user.id, day)),
                }
            )

        rows.append({
            "user": user,
            "cells": cells,
            "total_hours": sum(cell["duty"].hours for cell in cells if cell["duty"]),
        })

    previous_month = date(year, month, 1) - timedelta(days=1)
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    current_year = timezone.now().date().year
    available_years = set(range(current_year - 2, current_year + 4))
    available_years.add(year)

    return {
        "rows": rows,
        "days": days,
        "year": year,
        "month": month,
        "month_name": MONTH_NAMES[month - 1],
        "month_options": list(enumerate(MONTH_NAMES, 1)),
        "year_options": sorted(available_years),
        "previous_year": previous_month.year,
        "previous_month": previous_month.month,
        "next_year": next_month.year,
        "next_month": next_month.month,
        "month_value": f"{year:04d}-{month:02d}",
        "schedule_month": schedule_month,
        "first_employee": users[0] if users else None,
        "selected_team": team,
    }


@login_required
def month_schedule(request):
    year, month = _selected_period(request)
    team, teams = _selected_team(request)
    context = _schedule_data(year, month, team)
    context.update({
        "teams": teams,
        "can_manage": can_manage_team(request.user, team),
        "can_participate": participates_in_team(request.user, team),
    })
    return render(request, "work_schedule/month.html", context)


def _xlsx_response(rows, days, year, month, schedule_month):
    """Build a compact printable schedule matching the department Excel template."""
    weekday_names = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    absence_labels = {
        EmployeeAbsence.Type.VACATION: "От",
        EmployeeAbsence.Type.SICK_LEAVE: "Бл",
        EmployeeAbsence.Type.TRAINING: "Об",
    }

    def column_name(number):
        letters = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            letters = chr(65 + remainder) + letters
        return letters

    def numeric_text(value):
        if isinstance(value, Decimal):
            return format(value, "f").rstrip("0").rstrip(".") or "0"
        return str(value)

    def cell(reference, value=None, style=0):
        if value is None or value == "":
            return f'<c r="{reference}" s="{style}"/>'
        if isinstance(value, (int, float, Decimal)):
            return f'<c r="{reference}" s="{style}"><v>{numeric_text(value)}</v></c>'
        return (
            f'<c r="{reference}" s="{style}" t="inlineStr">'
            f'<is><t>{escape(str(value))}</t></is></c>'
        )

    def formula_cell(reference, formula, cached_value, style):
        return (
            f'<c r="{reference}" s="{style}"><f>{escape(formula)}</f>'
            f'<v>{numeric_text(cached_value)}</v></c>'
        )

    first_day_column = 3
    last_day_column = first_day_column + len(days) - 1
    sum_column = last_day_column + 1
    norm_column = sum_column + 1
    last_day_letter = column_name(last_day_column)
    sum_letter = column_name(sum_column)
    norm_letter = column_name(norm_column)
    last_data_row = 4 + len(rows)
    bottom_row = last_data_row + 1

    row_xml = ['<row r="1" ht="16" customHeight="1"/>']
    title_cells = [cell("B2", MONTH_NAMES[month - 1], 1)]
    title_cells.extend(
        cell(f"{column_name(column)}2", None, 1)
        for column in range(3, norm_column + 1)
    )
    row_xml.append(
        f'<row r="2" ht="26" customHeight="1">{"".join(title_cells)}</row>'
    )

    header_cells = [cell("B3", None, 2)]
    date_cells = [cell("B4", None, 2)]
    for index, day in enumerate(days, first_day_column):
        current_date = date(year, month, day["number"])
        column = column_name(index)
        header_cells.append(cell(f"{column}3", weekday_names[current_date.weekday()], 3))
        date_cells.append(cell(f"{column}4", day["number"], 7 if day["is_weekend"] else 6))
    header_cells.append(cell(f"{sum_letter}3", "Sum", 4))
    header_cells.append(
        cell(
            f"{norm_letter}3",
            schedule_month.main_employee_hours if schedule_month else None,
            5,
        )
    )
    date_cells.extend((cell(f"{sum_letter}4", None, 4), cell(f"{norm_letter}4", None, 12)))
    row_xml.append(f'<row r="3" ht="20" customHeight="1">{"".join(header_cells)}</row>')
    row_xml.append(f'<row r="4" ht="20" customHeight="1">{"".join(date_cells)}</row>')

    for row_number, row in enumerate(rows, 5):
        cells = [cell(f"B{row_number}", row["user"].full_name, 8)]
        for column_number, (day, schedule_cell) in enumerate(
            zip(days, row["cells"]),
            first_day_column,
        ):
            style = 10 if day["is_weekend"] else 9
            value = None
            if schedule_cell["absence"]:
                value = absence_labels.get(
                    schedule_cell["absence"].absence_type,
                    schedule_cell["absence"].get_absence_type_display(),
                )
            elif schedule_cell["duty"]:
                value = schedule_cell["duty"].hours
                if not schedule_cell["duty"].generated:
                    style = 13
            elif schedule_cell["preference_status"] == DutyDatePreference.Status.UNAVAILABLE:
                value = "-"
            cells.append(cell(f"{column_name(column_number)}{row_number}", value, style))
        cells.append(
            formula_cell(
                f"{sum_letter}{row_number}",
                f"SUM(C{row_number}:{last_day_letter}{row_number})",
                row["total_hours"],
                11,
            )
        )
        cells.append(cell(f"{norm_letter}{row_number}", None, 12))
        row_xml.append(
            f'<row r="{row_number}" ht="20" customHeight="1">{"".join(cells)}</row>'
        )

    bottom_cells = [cell(f"B{bottom_row}", None, 8)]
    bottom_cells.extend(
        cell(
            f"{column_name(column_number)}{bottom_row}",
            None,
            10 if day["is_weekend"] else 9,
        )
        for column_number, day in enumerate(days, first_day_column)
    )
    bottom_cells.extend(
        (cell(f"{sum_letter}{bottom_row}", None, 11), cell(f"{norm_letter}{bottom_row}", None, 12))
    )
    row_xml.append(
        f'<row r="{bottom_row}" ht="12" customHeight="1">{"".join(bottom_cells)}</row>'
    )

    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{norm_letter}{bottom_row}"/>'
        '<sheetViews><sheetView workbookViewId="0" showGridLines="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="20"/>'
        '<cols><col min="1" max="1" width="1.5" customWidth="1"/>'
        '<col min="2" max="2" width="18" customWidth="1"/>'
        f'<col min="3" max="{last_day_column}" width="4" customWidth="1"/>'
        f'<col min="{sum_column}" max="{sum_column}" width="6" customWidth="1"/>'
        f'<col min="{norm_column}" max="{norm_column}" width="6" customWidth="1"/></cols>'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        f'<mergeCells count="1"><mergeCell ref="B2:{norm_letter}2"/></mergeCells>'
        '<pageMargins left="0.5" right="0.5" top="0.5" bottom="0.5" header="0.25" footer="0.25"/>'
        '<pageSetup fitToHeight="1" fitToWidth="1" orientation="landscape" pageOrder="downThenOver"/>'
        '<headerFooter><oddFooter>&amp;CСтраница &amp;P</oddFooter></headerFooter>'
        '</worksheet>'
    )

    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font>'
        '<font><sz val="15"/><name val="Calibri"/></font></fonts>'
        '<fills count="8"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFDDDDDD"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFBDC0BF"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF72FCE9"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF88F94E"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFDBDBDB"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE2D9F3"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border><left style="thin"><color rgb="FFA5A5A5"/></left>'
        '<right style="thin"><color rgb="FFA5A5A5"/></right>'
        '<top style="thin"><color rgb="FFA5A5A5"/></top>'
        '<bottom style="thin"><color rgb="FFA5A5A5"/></bottom><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="14">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="1" fillId="3" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="1" fillId="4" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="1" fillId="5" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="6" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="7" borderId="1" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
        '</cellXfs><cellStyles count="1"><cellStyle name="Обычный" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )

    sheet_name = escape(MONTH_NAMES[month - 1], {'"': "&quot;"})
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        )
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="schedule-{year:04d}-{month:02d}.xlsx"'
    return response


@login_required
def export_month_schedule(request):
    year, month = _selected_period(request)
    team, _teams = _selected_team(request)
    context = _schedule_data(year, month, team)
    return _xlsx_response(
        context["rows"],
        context["days"],
        year,
        month,
        context["schedule_month"],
    )


def _preference_user(request, schedule_month, user_id):
    if user_id is None:
        if not participates_in_team(request.user, schedule_month.team):
            raise PermissionDenied("У администратора нет собственных пожеланий к графику.")
        return request.user
    if not can_manage_team(request.user, schedule_month.team):
        raise PermissionDenied("Редактировать пожелания может только заведующий этого коллектива.")
    return get_object_or_404(
        User,
        id=user_id,
        is_active=True,
        team_memberships__team=schedule_month.team,
        team_memberships__participates_in_schedule=True,
    )


@login_required
def preference_calendar(request, month_id, user_id=None):
    schedule_month = get_object_or_404(ScheduleMonth, id=month_id)
    if not can_access_team(request.user, schedule_month.team):
        raise PermissionDenied("Нет доступа к этому коллективу.")
    can_manage = can_manage_team(request.user, schedule_month.team)
    if user_id is None and can_manage and not participates_in_team(
        request.user,
        schedule_month.team,
    ):
        first_employee = (
            User.objects.filter(
                is_active=True,
                team_memberships__team=schedule_month.team,
                team_memberships__participates_in_schedule=True,
            )
            .order_by("full_name")
            .first()
        )
        if first_employee is None:
            raise PermissionDenied("Сначала добавьте хотя бы одного сотрудника.")
        return redirect(
            "admin_preference_calendar",
            month_id=month_id,
            user_id=first_employee.id,
        )
    selected_user = _preference_user(request, schedule_month, user_id)

    preferences = {
        preference.date: preference.status
        for preference in DutyDatePreference.objects.filter(
            month=schedule_month,
            user=selected_user,
        )
    }
    holiday_dates = set(
        schedule_month.holidays.values_list("date", flat=True)
    )
    calendar_weeks = []
    for week in calendar.monthcalendar(schedule_month.year, schedule_month.month):
        cells = []
        for day in week:
            current_date = (
                date(schedule_month.year, schedule_month.month, day)
                if day
                else None
            )
            cells.append(
                {
                    "day": day,
                    "date": current_date,
                    "status": preferences.get(current_date),
                    "is_holiday": current_date in holiday_dates,
                    "is_weekend": current_date and (
                        current_date.weekday() >= 5 or current_date in holiday_dates
                    ),
                }
            )
        calendar_weeks.append(cells)

    employees = User.objects.none()
    completed_preference_count = 0
    if can_manage:
        employees = User.objects.filter(
            is_active=True,
            team_memberships__team=schedule_month.team,
            team_memberships__participates_in_schedule=True,
        ).order_by("full_name").annotate(
            preference_updated_at=Max(
                "preference_activities__updated_at",
                filter=Q(preference_activities__month=schedule_month),
            )
        )
        completed_preference_count = sum(
            employee.preference_updated_at is not None
            for employee in employees
        )

    context = {
        "schedule_month": schedule_month,
        "selected_user": selected_user,
        "calendar_weeks": calendar_weeks,
        "employees": employees,
        "completed_preference_count": completed_preference_count,
        "can_manage": can_manage,
    }
    return render(request, "work_schedule/preferences_calendar.html", context)


@login_required
def set_preference(request, month_id, user_id=None):
    if request.method != "POST":
        return HttpResponseBadRequest("Нужен POST-запрос.")

    schedule_month = get_object_or_404(ScheduleMonth, id=month_id)
    if not can_access_team(request.user, schedule_month.team):
        raise PermissionDenied("Нет доступа к этому коллективу.")
    selected_user = _preference_user(request, schedule_month, user_id)
    try:
        preference_date = date.fromisoformat(request.POST["date"])
    except (KeyError, ValueError):
        return HttpResponseBadRequest("Некорректная дата.")

    if (preference_date.year, preference_date.month) != (
        schedule_month.year,
        schedule_month.month,
    ):
        return HttpResponseBadRequest("Дата должна относиться к выбранному месяцу.")

    status = request.POST.get("status")
    if status == "CLEAR":
        DutyDatePreference.objects.filter(
            month=schedule_month,
            user=selected_user,
            date=preference_date,
        ).delete()
    elif status in DutyDatePreference.Status.values:
        DutyDatePreference.objects.update_or_create(
            month=schedule_month,
            user=selected_user,
            date=preference_date,
            defaults={"status": status},
        )
    else:
        return HttpResponseBadRequest("Некорректный статус предпочтения.")

    if request.user == selected_user:
        PreferenceActivity.objects.update_or_create(
            month=schedule_month,
            user=selected_user,
        )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse(
            {
                "ok": True,
                "date": preference_date.isoformat(),
                "status": status,
            }
        )

    if can_manage_team(request.user, schedule_month.team) and user_id is not None:
        return redirect("admin_preference_calendar", month_id=month_id, user_id=user_id)
    return redirect("preference_calendar", month_id=month_id)

def generate_schedule(request, month_id):

    if not request.user.is_authenticated:
        return redirect("login")
    month = get_object_or_404(
        ScheduleMonth,
        id=month_id
    )
    if not can_manage_team(request.user, month.team):
        raise PermissionDenied("Генерировать график может только заведующий этого коллектива.")

    if request.method == "POST":

        generator = ScheduleGenerator(month)

        generator.generate()

        return redirect(
            "month_schedule"
        )


    permitted_shifts = ShiftType.objects.filter(
        team=month.team,
        use_in_generation=True,
    )
    if month.team.schedule_rules == Team.ScheduleRules.NURSES:
        permitted_shifts = permitted_shifts.filter(hours=23)

    weekday_shifts = permitted_shifts.filter(day_type=ShiftType.DayType.WEEKDAY)
    weekend_shifts = permitted_shifts.filter(day_type=ShiftType.DayType.WEEKEND)
    if month.team.schedule_rules == Team.ScheduleRules.NURSES:
        weekday_shifts = weekday_shifts or permitted_shifts
        weekend_shifts = weekend_shifts or permitted_shifts

    return render(
        request,
        "work_schedule/generate_schedule.html",
        {
            "month": month,
            "weekday_shifts": weekday_shifts,
            "weekend_shifts": weekend_shifts,
            "nurse_shifts": permitted_shifts,
            "uses_nurse_rules": (
                month.team.schedule_rules == Team.ScheduleRules.NURSES
            ),
        }
    )
