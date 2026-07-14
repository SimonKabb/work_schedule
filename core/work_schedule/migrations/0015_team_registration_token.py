import uuid

from django.db import migrations, models


def create_registration_tokens(apps, schema_editor):
    Team = apps.get_model("work_schedule", "Team")
    for team in Team.objects.all():
        team.registration_token = uuid.uuid4()
        team.save(update_fields=["registration_token"])


class Migration(migrations.Migration):
    dependencies = [
        ("work_schedule", "0014_scope_preferences_to_month"),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="registration_token",
            field=models.UUIDField(editable=False, null=True, verbose_name="Токен регистрации"),
        ),
        migrations.RunPython(create_registration_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="team",
            name="registration_token",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name="Токен регистрации"),
        ),
    ]
