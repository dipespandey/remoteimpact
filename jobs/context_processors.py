"""
Custom context processors for SEO and site-wide variables.
"""
import hashlib
from functools import lru_cache

from django.conf import settings


@lru_cache(maxsize=1)
def css_version():
    """Content hash of the stylesheet, so a new build is never served from cache.

    Static files are served under unhashed names with a multi-hour cache, so the
    link carries ?v=<hash>. Workers re-read it on reload (HUP).
    """
    for directory in getattr(settings, "STATICFILES_DIRS", []):
        try:
            with open(f"{directory}/tailwind.min.css", "rb") as fh:
                return hashlib.md5(fh.read()).hexdigest()[:10]
        except OSError:
            continue
    return "1"


def site_settings(request):
    """Add site URL and name to template context for SEO."""
    return {
        'SITE_URL': getattr(settings, 'SITE_URL', 'https://remoteimpact.org'),
        'SITE_NAME': getattr(settings, 'SITE_NAME', 'Remote Impact'),
        'CSS_VERSION': css_version(),
    }
