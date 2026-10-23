from functools import wraps

from django.conf import settings
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers


def cache_public_page(timeout):
    """Cache anonymous GETs without ever caching authenticated or cross-cookie HTML.

    Vary must be set inside the page-cache decorator, before it stores a response;
    setting it later in middleware does not protect the origin cache's lookup key.
    """
    def decorate(view):
        varied_view = vary_on_headers("Cookie", "HX-Request")(view)

        @wraps(view)
        def fresh_view(request, *args, **kwargs):
            request._performance_cache_status = "fresh"
            return varied_view(request, *args, **kwargs)

        cached = cache_page(timeout)(fresh_view)

        @wraps(view)
        def wrapped(request, *args, **kwargs):
            user = getattr(request, "user", None)
            if not settings.PUBLIC_PAGE_CACHE_ENABLED or (user and user.is_authenticated) or request.method not in ("GET", "HEAD"):
                request._performance_cache_status = "fresh"
                return view(request, *args, **kwargs)
            response = cached(request, *args, **kwargs)
            if not hasattr(request, "_performance_cache_status"):
                request._performance_cache_status = "hit"
            return response

        return wrapped
    return decorate
