from datetime import timedelta

from django import template
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.template.defaultfilters import linebreaks_filter

register = template.Library()


@register.filter(name="add_days")
def add_days(value, days):
    """Add days to a date/datetime value."""
    if value is None:
        return None
    try:
        return value + timedelta(days=int(days))
    except (TypeError, ValueError):
        return value


@register.filter(name="default_expiry")
def default_expiry(job):
    """Return expires_at or posted_at + 90 days for structured data."""
    if job.expires_at:
        return job.expires_at
    return job.posted_at + timedelta(days=90)


@register.filter(name="job_description_for_schema")
def job_description_for_schema(job):
    """Return the best available description for structured data schema."""
    # Priority: description > requirements > impact > organization description
    for field in [job.description, job.requirements, job.impact]:
        if field and field.strip():
            return field
    if job.organization and job.organization.description:
        return job.organization.description
    return job.title  # Fallback to title if nothing else


@register.filter(name="get_item")
def get_item(dictionary, key):
    """Get an item from a dictionary using a variable key."""
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter(name="render_html")
def render_html(value):
    """
    Renders value as safe HTML if it looks like HTML, otherwise applies linebreaks.
    Simple heuristic: if it contains HTML tags, assume it's HTML.
    """
    if not value:
        return ""

    value_str = str(value)
    # Check for common HTML indicators (opening or closing tags)
    html_indicators = [
        "</p>",
        "</div>",
        "</span>",
        "</a>",
        "</ul>",
        "</ol>",
        "</li>",
        "</h1>",
        "</h2>",
        "</h3>",
        "</h4>",
        "</strong>",
        "</em>",
        "</b>",
        "</i>",
        "<br>",
        "<br/>",
        "<br />",
        "<p>",
        "<ul>",
        "<ol>",
        "<li>",
    ]
    if any(tag in value_str.lower() for tag in html_indicators):
        return mark_safe(value_str)

    return linebreaks_filter(value)
