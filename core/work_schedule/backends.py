from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class UsernameOrFullNameBackend(ModelBackend):
    """Authenticate an employee by username or their unique full name."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = username or kwargs.get("username")
        if not identifier or password is None:
            return None

        UserModel = get_user_model()
        users = UserModel.objects.filter(
            Q(username__iexact=identifier.strip())
            | Q(full_name__iexact=identifier.strip())
        )
        if users.count() != 1:
            UserModel().set_password(password)
            return None

        user = users.get()
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
