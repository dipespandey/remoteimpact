from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0035_application_richer_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="job",
            name="application_url",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Optional external URL to apply for this job",
            ),
        ),
        migrations.AddField(
            model_name="job",
            name="custom_questions",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Custom application questions for on-platform applicants",
            ),
        ),
        migrations.AddField(
            model_name="application",
            name="custom_question_answers",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Answers to the job poster's custom application questions",
            ),
        ),
    ]
