from django.urls import path
from .views import month_schedule

urlpatterns = [
    path("", month_schedule, name="month_schedule"),
]