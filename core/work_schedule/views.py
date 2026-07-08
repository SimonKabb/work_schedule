# scheduler/views.py

import calendar
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .models import User, Duty, ScheduleMonth
from .services.schedule_generator import ScheduleGenerator


def month_schedule(request):
    today = timezone.now().date()

    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))

    days_in_month = calendar.monthrange(year, month)[1]

    users = User.objects.order_by("full_name")

    duties = (
        Duty.objects.filter(
            date__year=year,
            date__month=month,
        )
        .select_related("user", "shift_type")
    )

    # Словарь для быстрого поиска смен
    duties_map = {
        (duty.user_id, duty.date.day): duty
        for duty in duties
    }

    rows = []

    for user in users:
        cells = []

        for day in range(1, days_in_month + 1):
            duty = duties_map.get((user.id, day))
            cells.append(duty)

        rows.append({
            "user": user,
            "cells": cells,
        })

    context = {
        "rows": rows,
        "days": range(1, days_in_month + 1),
        "year": year,
        "month": month,
    }

    return render(request, "work_schedule/month.html", context)

def generate_schedule(request, month_id):

    month = get_object_or_404(
        ScheduleMonth,
        id=month_id
    )

    if request.method == "POST":

        generator = ScheduleGenerator(month)

        generator.generate()

        return redirect(
            "schedule_month",
            month_id=month.id
        )


    return render(
        request,
        "work_schedule/generate_schedule.html",
        {
            "month": month
        }
    )
