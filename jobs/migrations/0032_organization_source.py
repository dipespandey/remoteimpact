# Generated migration for Organization source tracking

from django.db import migrations, models


def backfill_organization_source(apps, schema_editor):
    """
    Backfill source field for existing organizations.
    All existing orgs are scraped (no direct signups yet).
    Try to infer source_detail from job sources.
    """
    Organization = apps.get_model('jobs', 'Organization')
    Job = apps.get_model('jobs', 'Job')
    
    # All existing orgs are scraped
    Organization.objects.all().update(source='scraped')
    
    # Try to set source_detail based on jobs
    for org in Organization.objects.all():
        # Get the most common source from this org's jobs
        job_sources = (
            Job.objects
            .filter(organization=org)
            .exclude(source__isnull=True)
            .exclude(source='')
            .values_list('source', flat=True)
        )
        if job_sources:
            # Use the first source we find
            source_detail = job_sources.first()
            if source_detail:
                org.source_detail = source_detail[:100]
                org.save(update_fields=['source_detail'])


def reverse_backfill(apps, schema_editor):
    """Reverse migration - just clear the fields."""
    Organization = apps.get_model('jobs', 'Organization')
    Organization.objects.all().update(source='scraped', source_detail='')


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0031_profileview'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='source',
            field=models.CharField(
                choices=[
                    ('scraped', 'Scraped from job boards'),
                    ('signup', 'Direct employer signup'),
                    ('claimed', 'Scraped then claimed by user'),
                    ('manual', 'Manually added by staff'),
                ],
                default='scraped',
                help_text='How this organization was added to the platform',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='organization',
            name='source_detail',
            field=models.CharField(
                blank=True,
                help_text="Specific source e.g., 'climatebase', 'idealist', 'lever'",
                max_length=100,
            ),
        ),
        migrations.RunPython(backfill_organization_source, reverse_backfill),
    ]
