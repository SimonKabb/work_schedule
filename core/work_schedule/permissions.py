from .models import Team, TeamMembership


def accessible_teams(user):
    if not user.is_authenticated:
        return Team.objects.none()
    if user.is_superuser:
        return Team.objects.filter(is_active=True)
    return Team.objects.filter(
        is_active=True,
        memberships__user=user,
    ).distinct()


def manageable_teams(user):
    if not user.is_authenticated:
        return Team.objects.none()
    if user.is_superuser:
        return Team.objects.filter(is_active=True)
    return Team.objects.filter(
        is_active=True,
        memberships__user=user,
        memberships__role=TeamMembership.Role.MANAGER,
    ).distinct()


def can_access_team(user, team):
    return accessible_teams(user).filter(pk=team.pk).exists()


def can_manage_team(user, team):
    return manageable_teams(user).filter(pk=team.pk).exists()


def participates_in_team(user, team):
    return TeamMembership.objects.filter(
        team=team,
        user=user,
        participates_in_schedule=True,
        user__is_active=True,
    ).exists()
