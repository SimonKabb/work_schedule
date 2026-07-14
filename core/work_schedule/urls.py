from django.urls import path
from .views import (
    annual_vacations,
    export_month_schedule,
    generate_schedule,
    month_schedule,
    preference_calendar,
    register_employee,
    reset_team_invitation,
    set_vacation,
    set_preference,
    team_invitation,
    team_invitation_qr,
)

urlpatterns = [
    path("", month_schedule, name="month_schedule"),
    path("join/<uuid:token>/", register_employee, name="register_employee"),
    path("teams/<int:team_id>/invitation/", team_invitation, name="team_invitation"),
    path("teams/<int:team_id>/invitation/qr.svg", team_invitation_qr, name="team_invitation_qr"),
    path("teams/<int:team_id>/invitation/reset/", reset_team_invitation, name="reset_team_invitation"),
    path("teams/<int:team_id>/vacations/", annual_vacations, name="annual_vacations"),
    path("teams/<int:team_id>/vacations/<int:user_id>/set/", set_vacation, name="set_vacation"),
    path("export/", export_month_schedule, name="export_month_schedule"),
    path("<int:month_id>/generate/", generate_schedule, name="generate_schedule"),
    path("<int:month_id>/preferences/", preference_calendar, name="preference_calendar"),
    path(
        "<int:month_id>/preferences/<int:user_id>/",
        preference_calendar,
        name="admin_preference_calendar",
    ),
    path("<int:month_id>/preferences/set/", set_preference, name="set_preference"),
    path(
        "<int:month_id>/preferences/<int:user_id>/set/",
        set_preference,
        name="admin_set_preference",
    ),
]
