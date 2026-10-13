from django.db import migrations


# Operational PostgreSQL expression indexes for the JSON comparisons used by
# trust_stats. Keep exact operators aligned with Django's JSONField lookups.
INDEXES = {
    'job_inactive_checked_idx': "(raw_data #> ARRAY['source_verification', 'checked_at'])",
    'job_inactive_removed_idx': "(raw_data -> 'removed_from_source_at')",
}


def create_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for name, expression in INDEXES.items():
        schema_editor.execute(
            f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} '
            f'ON jobs_job (({expression})) WHERE NOT is_active'
        )


def drop_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for name in INDEXES:
        schema_editor.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {name}')


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('jobs', '0076_job_indexing_state')]
    operations = [migrations.RunPython(create_indexes, drop_indexes)]
