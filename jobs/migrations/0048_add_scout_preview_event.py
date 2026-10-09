from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("jobs", "0047_add_job_quality_fields")]

    operations = [
        migrations.AlterField(
            model_name="consumerfunnelevent",
            name="event",
            field=models.CharField(
                choices=[
                    ("scout_previewed", "Scout previewed"),
                    ("scout_created", "Scout created"),
                    ("initial_email_sent", "Initial email sent"),
                    ("initial_email_failed", "Initial email failed"),
                    ("upgrade_clicked", "Upgrade clicked"),
                    ("checkout_started", "Checkout started"),
                    ("checkout_failed", "Checkout failed"),
                    ("payment_completed", "Payment completed"),
                    ("subscription_cancelled", "Subscription cancelled"),
                    ("match_relevant", "Match marked relevant"),
                    ("match_not_relevant", "Match marked not relevant"),
                ],
                max_length=32,
            ),
        )
    ]
