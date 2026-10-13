from django.db import migrations


def enable_similarity(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


class Migration(migrations.Migration):
    dependencies = [("jobs", "0072_atomic_api_rate_limits")]
    operations = [migrations.RunPython(enable_similarity, migrations.RunPython.noop)]
