from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0024_fix_duplicate_categories'),
    ]

    operations = [
        migrations.AddField(
            model_name='seekerprofile',
            name='work_styles',
            field=models.JSONField(default=list, help_text="List of work style values, e.g. ['builder', 'strategist']"),
        ),
    ]
