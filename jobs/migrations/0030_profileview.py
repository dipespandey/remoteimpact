from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("jobs", "0029_referral_and_job_alerts"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProfileView",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("viewed_at", models.DateTimeField(auto_now_add=True)),
                ("seeker", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="profile_views", to="jobs.seekerprofile")),
                ("viewer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="viewed_profiles", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-viewed_at"],
                "indexes": [
                    models.Index(fields=["seeker", "-viewed_at"], name="jobs_profileview_seeker_viewed"),
                ],
            },
        ),
    ]
