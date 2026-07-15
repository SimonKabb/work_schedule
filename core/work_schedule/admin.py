from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django import forms
from django.db.models import Q
from .models import (
    Duty,
    DutyDatePreference,
    PartTimeWorkload,
    ScheduleHoliday,
    ScheduleMonth,
    ShiftType,
    Team,
    TeamMembership,
    User,
)
from .permissions import manageable_teams


admin.site.site_header = "Управление графиком"
admin.site.site_title = "График смен"
admin.site.index_title = "Настройки и сотрудники"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (
        "username",
        "full_name",
        "employee_type",
        "is_staff",
        "is_active",
    )
    list_filter = ("employee_type", "is_staff", "is_active")
    search_fields = ("username", "full_name")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Профиль", {"fields": ("full_name", "employee_type")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Профиль", {"fields": ("full_name", "employee_type")}),
    )

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "schedule_rules", "is_active")
    list_filter = ("schedule_rules", "is_active")
    search_fields = ("name",)

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class TeamScopedAdminMixin:
    team_lookup = "team"

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(
            **{f"{self.team_lookup}__in": manageable_teams(request.user)}
        ).distinct()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            teams = manageable_teams(request.user)
            if db_field.name == "team":
                kwargs["queryset"] = teams
            elif db_field.name == "month":
                kwargs["queryset"] = ScheduleMonth.objects.filter(team__in=teams)
            elif db_field.name == "shift_type":
                kwargs["queryset"] = ShiftType.objects.filter(team__in=teams)
            elif db_field.name == "user":
                kwargs["queryset"] = User.objects.filter(
                    team_memberships__team__in=teams,
                    team_memberships__participates_in_schedule=True,
                ).distinct()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_list_filter(self, request):
        filters = super().get_list_filter(request)
        if request.user.is_superuser:
            return filters
        hidden_related_filters = {"team", "month", "shift_type"}
        return tuple(
            item
            for item in filters
            if not isinstance(item, str) or item not in hidden_related_filters
        )

    def _can_manage_object(self, request, obj):
        if request.user.is_superuser or obj is None:
            return True
        value = obj
        for part in self.team_lookup.split("__"):
            value = getattr(value, part)
        return manageable_teams(request.user).filter(pk=value.pk).exists()

    def has_module_permission(self, request):
        return request.user.is_superuser or manageable_teams(request.user).exists()

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request) and self._can_manage_object(request, obj)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request) and self._can_manage_object(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request) and self._can_manage_object(request, obj)


@admin.register(TeamMembership)
class TeamMembershipAdmin(TeamScopedAdminMixin, admin.ModelAdmin):
    list_display = ("user", "team", "role", "participates_in_schedule")
    list_filter = ("team", "role", "participates_in_schedule")
    search_fields = ("user__username", "user__full_name", "team__name")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "user" and not request.user.is_superuser:
            teams = manageable_teams(request.user)
            kwargs["queryset"] = User.objects.filter(
                Q(team_memberships__isnull=True)
                | Q(team_memberships__team__in=teams)
            ).distinct()
            return admin.ModelAdmin.formfield_for_foreignkey(
                self,
                db_field,
                request,
                **kwargs,
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        if request.user.is_superuser:
            return ()
        return ("role",)

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            obj.role = TeamMembership.Role.EMPLOYEE
        super().save_model(request, obj, form, change)
        if obj.role == TeamMembership.Role.MANAGER and not obj.user.is_staff:
            obj.user.is_staff = True
            obj.user.save(update_fields=["is_staff"])


@admin.register(DutyDatePreference)
class DutyDatePreferenceAdmin(TeamScopedAdminMixin, admin.ModelAdmin):
    team_lookup = "month__team"
    list_display = ("user", "date", "status", "month")
    list_filter = ("status", "month")
    search_fields = ("user__full_name",)


WEEKDAY_CHOICES = (
    ("0", "Понедельник"),
    ("1", "Вторник"),
    ("2", "Среда"),
    ("3", "Четверг"),
    ("4", "Пятница"),
    ("5", "Суббота"),
    ("6", "Воскресенье"),
)


class ScheduleMonthAdminForm(forms.ModelForm):
    increased_staff_weekdays = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Дни с увеличенным составом",
    )
    part_time_allowed_weekdays = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label="Разрешённые дни совместителей",
    )

    class Meta:
        model = ScheduleMonth
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial["increased_staff_weekdays"] = (
                self.instance.increased_staff_weekdays.split(",")
            )
            self.initial["part_time_allowed_weekdays"] = (
                self.instance.part_time_allowed_weekdays.split(",")
            )

    def clean_increased_staff_weekdays(self):
        return ",".join(self.cleaned_data["increased_staff_weekdays"])

    def clean_part_time_allowed_weekdays(self):
        return ",".join(self.cleaned_data["part_time_allowed_weekdays"])


class ScheduleHolidayInline(admin.TabularInline):
    model = ScheduleHoliday
    extra = 1
    fields = ("date",)
    verbose_name = "Праздничный день (работает как выходной)"
    verbose_name_plural = "Праздничные дни (используются смены выходного дня)"

    def _can_manage_parent(self, request, obj=None):
        if request.user.is_superuser:
            return True
        teams = manageable_teams(request.user)
        return teams.filter(pk=obj.team_id).exists() if obj else teams.exists()

    def has_view_permission(self, request, obj=None):
        return self._can_manage_parent(request, obj)

    def has_add_permission(self, request, obj=None):
        return self._can_manage_parent(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._can_manage_parent(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._can_manage_parent(request, obj)


@admin.register(ScheduleMonth)
class ScheduleMonthAdmin(TeamScopedAdminMixin, admin.ModelAdmin):
    form = ScheduleMonthAdminForm
    inlines = (ScheduleHolidayInline,)
    list_display = ("team", "year", "month", "main_employee_hours", "part_time_hours_percent")
    list_filter = ("team", "year")
    ordering = ("team", "-year", "-month")
    fieldsets = (
        ("Месяц", {"fields": ("team", "year", "month", "main_employee_hours")}),
        (
            "Усиленные дни",
            {
                "fields": ("increased_staff_weekdays", "increased_staff_count"),
                "description": "Применяется только к коллективам с правилами для врачей.",
            },
        ),
        (
            "Совместители",
            {
                "fields": ("part_time_allowed_weekdays", "part_time_hours_percent"),
                "description": "Ограничение дней применяется только к правилам для врачей.",
            },
        ),
    )


@admin.register(PartTimeWorkload)
class PartTimeWorkloadAdmin(TeamScopedAdminMixin, admin.ModelAdmin):
    team_lookup = "month__team"
    list_display = ("user", "month", "hours")
    list_filter = ("month",)


@admin.register(ShiftType)
class ShiftTypeAdmin(TeamScopedAdminMixin, admin.ModelAdmin):
    list_display = ("name", "team", "hours", "day_type", "use_in_generation")
    list_filter = ("team", "day_type", "use_in_generation")
    fields = ("team", "name", "hours", "day_type", "use_in_generation")


class DutyAdminForm(forms.ModelForm):
    class Meta:
        model = Duty
        fields = ("team", "user", "date", "shift_type")

    def clean(self):
        cleaned_data = super().clean()
        team = cleaned_data.get("team")
        user = cleaned_data.get("user")
        shift_type = cleaned_data.get("shift_type")
        if team and user and not TeamMembership.objects.filter(
            team=team,
            user=user,
            participates_in_schedule=True,
        ).exists():
            self.add_error("user", "Сотрудник не участвует в графике этого коллектива.")
        if team and shift_type and shift_type.team_id != team.id:
            self.add_error("shift_type", "Тип смены относится к другому коллективу.")
        if (
            team
            and shift_type
            and team.schedule_rules == Team.ScheduleRules.NURSES
            and shift_type.hours != 23
        ):
            self.add_error(
                "shift_type",
                "Для коллектива медсестёр можно назначить только 23-часовую смену.",
            )
        return cleaned_data


@admin.register(Duty)
class DutyAdmin(TeamScopedAdminMixin, admin.ModelAdmin):
    form = DutyAdminForm
    list_display = ("date", "team", "user", "shift_type", "generated")
    list_filter = ("team", "date", "shift_type", "generated")
    search_fields = ("user__full_name",)
    date_hierarchy = "date"
    ordering = ("-date", "user__full_name")
    fields = ("team", "user", "date", "shift_type", "generated")
    readonly_fields = ("generated",)

    def save_model(self, request, obj, form, change):
        # Any assignment saved by a person becomes fixed for future generations.
        obj.generated = False
        super().save_model(request, obj, form, change)
