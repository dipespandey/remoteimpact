from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0025_add_work_styles_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='seekerprofile',
            name='linkedin_url',
            field=models.URLField(blank=True, help_text='LinkedIn profile URL', max_length=200),
        ),
        migrations.AddField(
            model_name='seekerprofile',
            name='country_eligibility',
            field=models.CharField(blank=True, help_text='Country where user is eligible to work', max_length=100),
        ),
        migrations.AddField(
            model_name='seekerprofile',
            name='headline',
            field=models.CharField(blank=True, help_text="Short professional headline, e.g. 'Climate Policy Expert | Former UN Advisor'", max_length=150),
        ),
        migrations.AddField(
            model_name='seekerprofile',
            name='bio',
            field=models.TextField(blank=True, help_text='Professional bio, up to 500 words', max_length=2000),
        ),
    ]
