# scheduler/views.py

import calendar
import uuid
from datetime import date, timedelta
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
    PreferenceActivity,
    ScheduleMonth,
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

    users = list(
        User.objects.filter(
            is_active=True,
            team_memberships__team=team,
            team_memberships__participates_in_schedule=True,
        ).distinct().order_by("full_name")
    )

    schedule_month = ScheduleMonth.objects.filter(
        team=team,
        year=year,
        month=month,
    ).first()

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

    # Словарь для быстрого поиска смен
    duties_map = {
        (duty.user_id, duty.date.day): duty
        for duty in duties
    }

    days = [
        {
            "number": day,
            "is_weekend": date(year, month, day).weekday() >= 5,
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


def _xlsx_response(rows, days, year, month):
    table = [["Сотрудник", "Тип", *[str(day["number"]) for day in days], "Часы"]]
    for row in rows:
        employee_type = "осн" if row["user"].employee_type == User.EmployeeType.MAIN else "совм"
        table.append([
            row["user"].full_name,
            employee_type,
            *[
                cell["duty"].shift_type.name if cell["duty"] else ""
                for cell in row["cells"]
            ],
            row["total_hours"],
        ])

    def cell_xml(value, row_number, column_number):
        letters = ""
        number = column_number
        while number:
            number, remainder = divmod(number - 1, 26)
            letters = chr(65 + remainder) + letters
        reference = f"{letters}{row_number}"
        if isinstance(value, int):
            return f'<c r="{reference}" s="1"><v>{value}</v></c>'
        return f'<c r="{reference}" t="inlineStr" s="1"><is><t>{escape(str(value))}</t></is></c>'

    sheet_rows = "".join(
        f'<row r="{row_number}">' + "".join(
            cell_xml(value, row_number, column_number)
            for column_number, value in enumerate(row, 1)
        ) + "</row>"
        for row_number, row in enumerate(table, 1)
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<cols><col min="1" max="1" width="28" customWidth="1"/><col min="2" max="2" width="8" customWidth="1"/>'
        '<col min="3" max="40" width="13" customWidth="1"/></cols>'
        f'<sheetData>{sheet_rows}</sheetData></worksheet>'
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="График" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf></cellXfs></styleSheet>')
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    response = HttpResponse(output.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="schedule-{year:04d}-{month:02d}.xlsx"'
    return response


@login_required
def export_month_schedule(request):
    year, month = _selected_period(request)
    team, _teams = _selected_team(request)
    context = _schedule_data(year, month, team)
    return _xlsx_response(context["rows"], context["days"], year, month)


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
                    "is_weekend": current_date and current_date.weekday() >= 5,
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


    return render(
        request,
        "work_schedule/generate_schedule.html",
        {
            "month": month,
            "weekday_shifts": ShiftType.objects.filter(
                team=month.team,
                day_type=ShiftType.DayType.WEEKDAY
            ),
            "weekend_shifts": ShiftType.objects.filter(
                team=month.team,
                day_type=ShiftType.DayType.WEEKEND
            ),
        }
    )
