import ast
import re
from urllib.parse import urlsplit

from django.db import migrations


def _normalize(value):
    value = (value or "").strip()
    if value.startswith("{"):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return ""
        if not isinstance(parsed, dict):
            return ""
        value = parsed.get("en") or parsed.get("es") or parsed.get("pt") or ""
        if not isinstance(value, str):
            return ""
        value = value.strip()
        if value.startswith("/"):
            value = f"https://www.idealist.org{value}"

    if value.startswith("//"):
        value = f"https:{value}"
    elif not re.match(r"^https?://", value, flags=re.IGNORECASE):
        if re.match(
            r"^[a-z0-9.-]+\.[a-z]{2,}(?:[/:?#].*)?$",
            value,
            flags=re.IGNORECASE,
        ):
            value = f"https://{value}"
        else:
            return ""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    if any(character.isspace() for character in parsed.netloc):
        return ""
    return value


def normalize_organization_websites(apps, schema_editor):
    Organization = apps.get_model("jobs", "Organization")
    changed = []
    for organization in Organization.objects.exclude(website="").iterator(chunk_size=500):
        normalized = _normalize(organization.website)
        if normalized != organization.website:
            organization.website = normalized
            changed.append(organization)
        if len(changed) >= 500:
            Organization.objects.bulk_update(changed, ["website"], batch_size=500)
            changed = []
    if changed:
        Organization.objects.bulk_update(changed, ["website"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [("jobs", "0044_consumer_funnel_and_restore_scout_trials")]

    operations = [
        migrations.RunPython(
            normalize_organization_websites,
            migrations.RunPython.noop,
        )
    ]
