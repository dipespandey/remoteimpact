from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("jobs", "0027_remove_bio_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="DripEmailLog",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("drip_type", models.CharField(max_length=30, choices=[
                    ("day2_profile", "Day 2 – Complete Profile"),
                    ("day5_jobs", "Day 5 – Top Jobs"),
                    ("day10_assistant", "Day 10 – AI Assistant"),
                ])),
                ("sent_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="drip_emails", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-sent_at"],
                "unique_together": {("user", "drip_type")},
            },
        ),
    ]
