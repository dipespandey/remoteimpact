from datetime import timedelta
import re

from django import template
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.template.defaultfilters import linebreaks_filter
from jobs.services.html_sanitizer import sanitize_html

try:
    import markdown
    HAS_MARKDOWN = True
except ImportError:
    HAS_MARKDOWN = False

register = template.Library()


@register.filter
def scout_needs_confirmation(jobs):
    return any(getattr(job, 'scout_needs_confirmation', False) for job in (jobs or ()))


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
    """Return only an explicitly known expiry for structured data."""
    return job.expires_at


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
    - Sanitize imported HTML and generated markdown with a strict allowlist
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
    if any(tag in value_str.lower() for tag in html_indicators) or re.search(r"<[/!?a-zA-Z]", value_str):
        # A scraped description can contain its own H1 headings. The job page
        # already has one document-level H1, so keep imported content below it.
        value_str = re.sub(r"<h1(\s[^>]*)?>", r"<h2\1>", value_str, flags=re.IGNORECASE)
        value_str = re.sub(r"</h1\s*>", "</h2>", value_str, flags=re.IGNORECASE)
        return mark_safe(sanitize_html(value_str))

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
        return mark_safe(sanitize_html(html))
    
    # Fallback: just convert line breaks
    return linebreaks_filter(value)


@register.filter(name="compensation_display")
def compensation_display_filter(job):
    from jobs.services.scout_evidence import compensation_display
    return compensation_display(job)


_TITLE_ACRONYMS = {
    "US", "USA", "UK", "EU", "UN", "AI", "ML", "HR", "IT", "NGO", "INGO", "CEO", "CFO", "COO", "CTO",
    "CIO", "VP", "SVP", "EVP", "M&E", "MEL", "MEAL", "ESG", "QA", "UX", "UI", "R&D", "WASH", "HIV",
    "GIS", "SQL", "API", "PR", "SEO", "CRM", "ERP", "EHS", "HSE", "IP", "ICT", "LLM", "PHD", "MPH",
    "MBA", "GHG", "CSR", "DEI", "LGBTQ", "LGBTQ+", "NYC", "DC", "APAC", "EMEA", "LATAM", "MENA", "II", "III", "IV",
}
_TITLE_SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with", "via", "per"}


@register.filter(name="display_title")
def display_title(value):
    """Readable job titles: source feeds sometimes publish them in ALL CAPS."""
    text = str(value or "")
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8 or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return text

    def fix(word, first):
        core = word.strip("()[]/,.:-")
        if core.upper() in _TITLE_ACRONYMS:
            return word.upper()
        lowered = word.lower()
        if not first and core.lower() in _TITLE_SMALL:
            return lowered
        return re.sub(r"[a-z]", lambda m: m.group().upper(), lowered, count=1)

    words = text.split(" ")
    return " ".join(fix(word, index == 0) for index, word in enumerate(words))


SKILL_ACRONYMS = {"sql", "crm", "ui", "ux", "seo", "api", "ai", "ml", "hr", "it", "gis", "esg", "m&e", "mel", "r"}


@register.filter(name="skill_label")
def skill_label(slug):
    """'system-design' -> 'System design', 'sql' -> 'SQL'."""
    words = str(slug or "").replace("_", "-").split("-")
    words = [w.upper() if w in SKILL_ACRONYMS else w for w in words if w]
    if not words:
        return ""
    first = words[0] if words[0].isupper() else words[0].capitalize()
    return " ".join([first] + words[1:])


AREA_TONES = {
    "green": {"climate-environment", "coastal-ocean-sinks", "food-agriculture-land-use", "buildings",
              "transportation", "materials-manufacturing"},
    "amber": {"energy", "capital"},
    "indigo": {"ai-safety", "nuclear-security", "biosecurity", "technology"},
    "rose": {"global-health", "mental-health", "disability"},
    "orange": {"animal-welfare"},
    "sky": {"education", "children-youth", "communications"},
    "violet": {"policy-advocacy", "advocacy-or-policy", "human-rights", "civic-engagement",
               "gender-equality-social-inclusion"},
    "red": {"humanitarian"},
    "teal": {"community-development", "poverty-development", "nonprofit-charity", "effective-altruism"},
}
_SLUG_TONE = {slug: tone for tone, slugs in AREA_TONES.items() for slug in slugs}
_ORG_TONES = ["green", "indigo", "rose", "sky", "violet", "amber", "teal", "orange"]


@register.filter(name="area_tone")
def area_tone(category):
    """CSS tone class for an impact area (Category or slug)."""
    slug = getattr(category, "slug", category) or ""
    return f"tone-{_SLUG_TONE.get(str(slug), 'stone')}"


@register.filter(name="org_tone")
def org_tone(name):
    """Stable tone for an organization avatar, so the same org always gets the same colour."""
    import zlib
    return f"tone-{_ORG_TONES[zlib.crc32(str(name or '').encode()) % len(_ORG_TONES)]}"


@register.filter(name="initials")
def initials(name):
    words = [w for w in re.split(r"[\s\-_/&,.()]+", str(name or "")) if w and w[0].isalnum()]
    if not words:
        return "?"
    letters = (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper()
    return letters


@register.filter(name="listing_language")
def listing_language_filter(job):
    from jobs.services.listing_language import listing_language
    return listing_language(getattr(job, "title", ""), getattr(job, "description", ""))


@register.filter
def seo_trim(value, limit=160):
    """Trim rendered title/description text to fit search results, at a word boundary, entity-safe."""
    import html as _html
    from django.utils.html import escape
    from django.utils.safestring import mark_safe
    from jobs.services.seo_text import _trim
    text = _html.unescape(str(value or ""))
    return mark_safe(escape(_trim(text, int(limit))))
