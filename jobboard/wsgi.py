"""
WSGI config for jobboard project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'jobboard.settings')

application = get_wsgi_application()


def _warm_up():
    """Load the URL configuration and compile the busiest templates while the worker boots.

    Django does both lazily, so without this the first visitors after every deploy wait
    several seconds.
    """
    import logging

    try:
        from django.template.loader import get_template
        from django.urls import get_resolver

        get_resolver().url_patterns
        for name in ("base.html", "jobs/home.html", "jobs/job_list.html", "jobs/job_detail.html",
                     "jobs/partials/job_row.html", "account/dashboard.html", "jobs/category_landing.html",
                     "jobs/organization_profile.html"):
            try:
                get_template(name)
            except Exception:  # noqa: BLE001 - a renamed template must not stop the worker
                pass
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("Worker warm-up failed")


_warm_up()
