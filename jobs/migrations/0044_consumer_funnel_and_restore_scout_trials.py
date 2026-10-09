from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def restore_new_scout_trial_for_legacy_users(apps, schema_editor):
    AssistantSubscription = apps.get_model("jobs", "AssistantSubscription")
    JobAlert = apps.get_model("jobs", "JobAlert")

    natural_language_user_ids = JobAlert.objects.exclude(intent_text="").values_list(
        "user_id", flat=True
    )
    AssistantSubscription.objects.filter(
        free_job_scout_used_at__isnull=False
    ).exclude(user_id__in=natural_language_user_ids).update(
        free_job_scout_used_at=None
    )


class Migration(migrations.Migration):
    dependencies = [("jobs", "0043_make_free_job_scout_one_time")]

    operations = [
        migrations.CreateModel(
            name="ConsumerFunnelEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "event",
                    models.CharField(
                        choices=[
                            ("scout_created", "Scout created"),
                            ("initial_email_sent", "Initial email sent"),
                            ("initial_email_failed", "Initial email failed"),
                            ("upgrade_clicked", "Upgrade clicked"),
                            ("checkout_started", "Checkout started"),
                            ("checkout_failed", "Checkout failed"),
                            ("payment_completed", "Payment completed"),
                            ("subscription_cancelled", "Subscription cancelled"),
                        ],
                        max_length=32,
                    ),
                ),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "dedupe_key",
                    models.CharField(blank=True, max_length=160, null=True, unique=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "alert",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="funnel_events",
                        to="jobs.jobalert",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="consumer_funnel_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["event", "-created_at"],
                        name="jobs_consum_event_0a77c5_idx",
                    ),
                    models.Index(
                        fields=["user", "-created_at"],
                        name="jobs_consum_user_id_1cf173_idx",
                    ),
                ]
            },
        ),
        migrations.RunPython(
            restore_new_scout_trial_for_legacy_users, migrations.RunPython.noop
        ),
    ]
