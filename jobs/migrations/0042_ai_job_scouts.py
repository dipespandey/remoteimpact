from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [("jobs", "0041_add_checkout_failed_event")]

    operations = [
        migrations.AddField(
            model_name="jobalert",
            name="intent_text",
            field=models.TextField(
                blank=True,
                help_text="The user's natural-language description of the work they want",
            ),
        ),
        migrations.AddField(
            model_name="jobalert",
            name="parsed_preferences",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Structured criteria interpreted from intent_text",
            ),
        ),
        migrations.AlterField(
            model_name="jobalert",
            name="frequency",
            field=models.CharField(
                choices=[
                    ("instant", "As soon as possible"),
                    ("daily", "Daily"),
                    ("weekly", "Weekly"),
                ],
                default="daily",
                max_length=10,
            ),
        ),
        migrations.CreateModel(
            name="JobAlertDelivery",
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
                ("sent_at", models.DateTimeField(auto_now_add=True)),
                ("match_score", models.PositiveSmallIntegerField(default=0)),
                ("match_reasons", models.JSONField(blank=True, default=list)),
                (
                    "alert",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deliveries",
                        to="jobs.jobalert",
                    ),
                ),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="alert_deliveries",
                        to="jobs.job",
                    ),
                ),
            ],
            options={
                "ordering": ["-sent_at"],
                "indexes": [
                    models.Index(
                        fields=["alert", "-sent_at"],
                        name="jobs_jobale_alert_i_9e2095_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("alert", "job"), name="unique_job_alert_delivery"
                    )
                ],
            },
        ),
    ]
