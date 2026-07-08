# scheduler/views.py

import calendar

from django.shortcuts import render
from django.utils import timezone

from .models import User, Duty


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
