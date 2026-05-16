from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0034_job_payment_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="application",
            name="full_name",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="application",
            name="email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="application",
            name="phone",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="application",
            name="linkedin_url",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="application",
            name="portfolio_url",
            field=models.URLField(
                blank=True, help_text="Portfolio, website, or GitHub"
            ),
        ),
        migrations.AddField(
            model_name="application",
            name="years_experience",
            field=models.CharField(
                blank=True,
                choices=[
                    ("0-2", "0-2 years"),
                    ("3-5", "3-5 years"),
                    ("6-10", "6-10 years"),
                    ("10+", "10+ years"),
                ],
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="application",
            name="current_location",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="application",
            name="available_from",
            field=models.DateField(
                blank=True, help_text="Earliest start date", null=True
            ),
        ),
        migrations.AddField(
            model_name="application",
            name="willing_to_relocate",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="application",
            name="salary_expectation",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="application",
            name="why_great_fit",
            field=models.TextField(blank=True, help_text="Short pitch for this role"),
        ),
    ]
