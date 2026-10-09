from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("jobs", "0054_employer_interest_notifications")]

    operations = [
        migrations.AddField(
            model_name="assistantsubscription",
            name="stripe_checkout_session_id",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="assistantsubscription",
            name="stripe_checkout_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="consumerfunnelevent",
            name="event",
            field=models.CharField(
                choices=[
                    ("account_created", "Account created"),
                    ("scout_landed", "Scout page viewed"),
                    ("scout_job_viewed", "Scout job viewed"),
                    ("scout_previewed", "Scout previewed"),
                    ("scout_signup_started", "Scout signup started"),
                    ("scout_created", "Scout created"),
                    ("initial_email_sent", "Initial email sent"),
                    ("initial_email_failed", "Initial email failed"),
                    ("initial_matches_empty", "Initial Scout had no matches"),
                    ("upgrade_clicked", "Upgrade clicked"),
                    ("checkout_started", "Checkout started"),
                    ("checkout_created", "Stripe checkout created"),
                    ("checkout_expired", "Stripe checkout expired"),
                    ("checkout_failed", "Checkout failed"),
                    ("payment_completed", "Payment completed"),
                    ("subscription_cancelled", "Subscription cancelled"),
                    ("match_relevant", "Match marked relevant"),
                    ("match_not_relevant", "Match marked not relevant"),
                    ("shortlist_viewed", "Private shortlist viewed"),
                    ("interest_saved", "Job interest saved"),
                    ("employer_opt_in", "Candidate opted in to an employer"),
                    ("collection_created", "Job collection shared"),
                    ("collection_viewed", "Shared job collection viewed"),
                ],
                max_length=32,
            ),
        ),
    ]
