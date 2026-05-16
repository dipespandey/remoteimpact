from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0033_missionscreeningquestion_jobapplicationresponse_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="is_paid",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="job",
            name="stripe_checkout_session_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="job",
            name="stripe_payment_intent",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="job",
            name="paid_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
