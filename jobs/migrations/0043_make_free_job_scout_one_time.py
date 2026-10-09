from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def close_existing_free_scouts(apps, schema_editor):
    AssistantSubscription = apps.get_model("jobs", "AssistantSubscription")
    JobAlert = apps.get_model("jobs", "JobAlert")
    now = timezone.now()

    pro_user_ids = AssistantSubscription.objects.filter(
        is_subscribed=True
    ).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gte=now)
    ).values_list("user_id", flat=True)
    free_user_ids = list(
        JobAlert.objects.exclude(user_id__in=pro_user_ids)
        .values_list("user_id", flat=True)
        .distinct()
    )

    JobAlert.objects.filter(user_id__in=free_user_ids).update(
        frequency="once",
        is_active=False,
    )
    for user_id in free_user_ids:
        subscription, _ = AssistantSubscription.objects.get_or_create(user_id=user_id)
        if subscription.free_job_scout_used_at is None:
            subscription.free_job_scout_used_at = now
            subscription.save(update_fields=["free_job_scout_used_at"])


class Migration(migrations.Migration):
    dependencies = [("jobs", "0042_ai_job_scouts")]

    operations = [
        migrations.AddField(
            model_name="assistantsubscription",
            name="free_job_scout_used_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="jobalert",
            name="frequency",
            field=models.CharField(
                choices=[
                    ("once", "One-time"),
                    ("instant", "As soon as possible"),
                    ("daily", "Daily"),
                    ("weekly", "Weekly"),
                ],
                default="once",
                max_length=10,
            ),
        ),
        migrations.RunPython(close_existing_free_scouts, migrations.RunPython.noop),
    ]
