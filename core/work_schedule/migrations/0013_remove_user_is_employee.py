from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("work_schedule", "0012_teams"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="user",
            name="is_employee",
        ),
    ]
