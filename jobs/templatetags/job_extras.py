from django import template
from django.utils.safestring import mark_safe
from django.template.defaultfilters import linebreaks_filter

register = template.Library()


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
