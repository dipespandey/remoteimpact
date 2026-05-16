from datetime import timedelta
import re

from django import template
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.template.defaultfilters import linebreaks_filter

try:
    import markdown
    HAS_MARKDOWN = True
except ImportError:
    HAS_MARKDOWN = False

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


@register.filter(name="employment_type_schema")
def employment_type_schema(value):
    """Map local job type values to Schema.org/Google JobPosting values."""
    mapping = {
        "full-time": "FULL_TIME",
        "full_time": "FULL_TIME",
        "part-time": "PART_TIME",
        "part_time": "PART_TIME",
        "contract": "CONTRACTOR",
        "contractor": "CONTRACTOR",
        "freelance": "CONTRACTOR",
        "internship": "INTERN",
        "intern": "INTERN",
        "temporary": "TEMPORARY",
        "volunteer": "VOLUNTEER",
    }
    return mapping.get(str(value or "").lower(), "OTHER")


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
    Renders value as safe HTML.
    - If it looks like HTML, return as-is
    - If it looks like markdown, convert to HTML
    - Otherwise, apply linebreaks
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

    # Check for markdown indicators
    markdown_indicators = [
        r'^#{1,6}\s',      # Headers (# ## ### etc)
        r'^\*\*.*\*\*',    # Bold **text**
        r'^- ',            # Unordered list
        r'^\* ',           # Unordered list (asterisk)
        r'^• ',            # Bullet points
        r'^\d+\.\s',       # Ordered list
        r'\[.*\]\(.*\)',   # Links [text](url)
    ]
    
    is_markdown = any(
        re.search(pattern, value_str, re.MULTILINE)
        for pattern in markdown_indicators
    )
    
    if is_markdown and HAS_MARKDOWN:
        # Convert markdown to HTML
        # Clean up some common issues first
        cleaned = value_str
        # Convert bullet points (•) to standard markdown bullets
        cleaned = re.sub(r'^• ', '- ', cleaned, flags=re.MULTILINE)
        # Handle **bold** headers that start with ##
        cleaned = re.sub(r'^(#{1,6})\s*\*\*(.+?)\*\*\s*$', r'\1 \2', cleaned, flags=re.MULTILINE)
        
        html = markdown.markdown(
            cleaned,
            extensions=['nl2br', 'sane_lists'],
            output_format='html5'
        )
        return mark_safe(html)
    
    # Fallback: just convert line breaks
    return linebreaks_filter(value)
