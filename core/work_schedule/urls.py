from django.urls import path
from .views import month_schedule, generate_schedule

urlpatterns = [
    path("", month_schedule, name="month_schedule"),
    path("<int:month_id>/generate/", generate_schedule, name="generate_schedule"),
]