from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0026_add_linkedin_headline_bio'),
    ]

    operations = [
        migrations.AlterField(
            model_name='seekerprofile',
            name='bio',
            field=models.TextField(blank=True, help_text='Professional bio (optional)'),
        ),
    ]
