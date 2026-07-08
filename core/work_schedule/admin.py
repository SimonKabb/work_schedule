from django.contrib import admin
from .models import User, ScheduleMonth, DutyDatePreference, PartTimeWorkload, ShiftType, Duty


admin.site.register(User)
admin.site.register(DutyDatePreference)
admin.site.register(ScheduleMonth)
admin.site.register(PartTimeWorkload)
admin.site.register(ShiftType)
admin.site.register(Duty)