from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("work_schedule", "0013_remove_user_is_employee"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="dutydatepreference",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="dutydatepreference",
            constraint=models.UniqueConstraint(
                fields=("month", "user", "date"),
                name="unique_month_user_preference_date",
            ),
        ),
    ]
