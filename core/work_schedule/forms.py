from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


class EmployeeRegistrationForm(UserCreationForm):
    full_name = forms.CharField(
        max_length=255,
        label="Фамилия и инициалы",
        help_text="Например: Иванов И",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "full_name", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Логин"
        self.fields["username"].help_text = "Латинские буквы и цифры, например ivanov."
        self.fields["password1"].label = "Пароль"
        self.fields["password2"].label = "Повторите пароль"
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Такой логин уже используется.")
        return username
