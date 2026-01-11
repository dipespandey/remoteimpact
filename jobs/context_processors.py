"""
Custom context processors for SEO and site-wide variables.
"""
from django.conf import settings


def site_settings(request):
    """Add site URL and name to template context for SEO."""
    return {
        'SITE_URL': getattr(settings, 'SITE_URL', 'https://www.remoteimpact.io'),
        'SITE_NAME': getattr(settings, 'SITE_NAME', 'Remote Impact'),
    }
