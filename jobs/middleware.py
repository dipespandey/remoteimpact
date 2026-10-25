import logging
import random
import re
from time import monotonic
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag
from django.contrib import messages
from django.shortcuts import redirect
from django.utils.cache import patch_vary_headers
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin

from jobs.services.performance import emit_event, safe_route

logger = logging.getLogger(__name__)


def _accepts_markdown(value: str) -> bool:
    """Return whether the client explicitly requested a usable Markdown variant."""
    for item in value.lower().split(","):
        media_type, *parameters = item.strip().split(";")
        if media_type.strip() != "text/markdown":
            continue
        for parameter in parameters:
            name, separator, raw_value = parameter.strip().partition("=")
            if name == "q" and separator:
                try:
                    if float(raw_value) <= 0:
                        break
                except ValueError:
                    break
        else:
            return True
    return False


def _markdown_from_html(html: str, source_url: str) -> str:
    """Make a compact, readable Markdown representation of a public HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    content = soup.find("main") or soup.body or soup
    tab_labels = {
        element.get("id"): element.get_text(" ", strip=True)
        for element in content.find_all(attrs={"role": "tab"})
        if element.get("id") and element.get_text(" ", strip=True)
    }
    for element in content.find_all(["script", "style", "noscript", "template", "svg", "form", "button", "input", "select", "textarea"]):
        element.decompose()

    def render(node):
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ""
        name = node.name.lower()
        if name in {"script", "style", "noscript", "template", "svg", "form", "button", "input", "select", "textarea"}:
            return ""
        if name == "br":
            return "\n"
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            return "\n\n" + ("#" * int(name[1])) + " " + "".join(render(child) for child in node.children).strip() + "\n\n"
        if name == "a":
            text = "".join(render(child) for child in node.children).strip()
            href = (node.get("href") or "").strip()
            if not text or not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                return text
            return f"[{text}]({urljoin(source_url, href)})"
        if name in {"strong", "b"}:
            return "**" + "".join(render(child) for child in node.children).strip() + "**"
        if name in {"em", "i"}:
            return "*" + "".join(render(child) for child in node.children).strip() + "*"
        if name == "code":
            return "`" + node.get_text(" ", strip=True) + "`"
        if name == "pre":
            return "\n\n```\n" + node.get_text("\n", strip=True) + "\n```\n\n"
        if node.get("role") == "tabpanel":
            label_id = node.get("aria-labelledby")
            label = tab_labels.get(label_id, "")
            heading = f"## {label}\n\n" if label else ""
            return "\n\n" + heading + "".join(render(child) for child in node.children).strip() + "\n\n"
        if name in {"ul", "ol"}:
            rows = []
            for position, child in enumerate(node.find_all("li", recursive=False), start=1):
                nested = child.find(["ul", "ol"], recursive=False)
                nested_text = render(nested) if nested else ""
                if nested:
                    nested.extract()
                marker = f"{position}." if name == "ol" else "-"
                text = "".join(render(grandchild) for grandchild in child.children).strip()
                if text:
                    rows.append(f"{marker} {text}")
                if nested_text:
                    rows.append(nested_text.strip())
            return "\n" + "\n".join(rows) + "\n"
        if name == "li":
            return "\n- " + "".join(render(child) for child in node.children).strip()
        if name == "blockquote":
            text = "".join(render(child) for child in node.children).strip()
            return "\n\n" + "\n".join("> " + line for line in text.splitlines() if line.strip()) + "\n\n"
        if name == "tr":
            cells = [cell.get_text(" ", strip=True) for cell in node.find_all(["th", "td"], recursive=False)]
            return (" | ".join(cells) + "\n") if cells else ""
        if name in {"p", "div", "section", "article", "header", "footer", "main", "aside", "figure", "figcaption", "dl", "dt", "dd", "table"}:
            return "\n\n" + "".join(render(child) for child in node.children).strip() + "\n\n"
        return "".join(render(child) for child in node.children)

    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in render(content).splitlines()]
    output = "\n".join(lines)
    output = re.sub(r"\n{3,}", "\n\n", output).strip()
    if title and not re.search(r"^#\s+", output, re.MULTILINE):
        output = f"# {title}\n\n{output}"
    safe_title = (title or "Remote Impact").replace('"', "'")
    return f'---\ntitle: "{safe_title}"\nsource: {source_url}\n---\n\n{output}\n'


class MarkdownResponseMiddleware:
    """Serve a public HTML page as Markdown only when a client explicitly asks.

    This sits inside GZipMiddleware, so the Markdown variant is compressed as
    needed.  It runs outside page-view caching: Django keeps its cached HTML,
    then this middleware derives the requested variant and marks ``Vary:
    Accept`` for HTTP caches.
    """

    PRIVATE_PATH_PREFIXES = (
        "/admin/", "/accounts/", "/account/", "/dashboard/", "/onboarding/",
        "/api/", "/mcp/", "/checkout/", "/webhook/", "/applications/",
        "/my-matches/", "/impact-profile/", "/talent/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        wants_markdown = (
            request.method in {"GET", "HEAD"}
            and _accepts_markdown(request.headers.get("Accept", ""))
            and not request.path.startswith(self.PRIVATE_PATH_PREFIXES)
            and not getattr(getattr(request, "user", None), "is_authenticated", False)
        )
        response = self.get_response(request)
        if not wants_markdown:
            return response
        if (
            response.status_code not in {200, 404}
            or response.streaming
            or response.get("Content-Encoding")
            or not response.get("Content-Type", "").lower().startswith("text/html")
        ):
            return response

        markdown = _markdown_from_html(
            response.content.decode(response.charset or "utf-8"), request.build_absolute_uri()
        )
        response.content = markdown.encode("utf-8")
        response["Content-Type"] = "text/markdown; charset=utf-8"
        response["Content-Length"] = str(len(response.content))
        if response.has_header("ETag"):
            del response["ETag"]
        patch_vary_headers(response, ("Accept",))
        return response


class OutboundAttributionMiddleware:
    """Cover template and imported-content links, including HTMX fragments."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (response.streaming or response.get("Content-Encoding")
                or not response.get("Content-Type", "").startswith("text/html")
                or request.path.startswith("/admin/")):
            return response
        from jobs.services.outbound_links import attribute_html_links
        from urllib.parse import urlsplit

        original = response.content.decode(response.charset)
        updated = attribute_html_links(original, internal_hosts=(urlsplit(request.build_absolute_uri("/")).hostname,))
        if updated != original:
            response.content = updated.encode(response.charset)
            if response.has_header("Content-Length"):
                response["Content-Length"] = len(response.content)
            if response.has_header("ETag"):
                del response["ETag"]
        return response


class SecurityHeadersMiddleware:
    """Add conservative browser security headers site-wide."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # The early-career embed is a public, read-only directory preview. It is
        # intentionally embeddable by partner sites; all other pages remain
        # protected from framing.
        frame_ancestors = "*" if request.path == "/early-careers/embed/" else "'none'"

        from jobs.csp import policy_hashes
        scripts, handlers = [], []
        if not response.streaming and not response.get("Content-Encoding") and response.get("Content-Type", "").startswith("text/html"):
            scripts, handlers = policy_hashes(response.content.decode(response.charset or "utf-8"))
        script_policy = " ".join(scripts)
        handler_policy = " ".join(handlers)
        response["Content-Security-Policy"] = (
                "default-src 'self'; "
                f"script-src 'self' {script_policy} "
                "https://www.googletagmanager.com https://www.google-analytics.com "
                "https://js.stripe.com https://www.gstatic.com; "
                f"script-src-attr 'unsafe-hashes' {handler_policy}; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com data:; "
                "img-src 'self' data: https:; "
                "connect-src 'self' https://www.google-analytics.com https://region1.google-analytics.com https://api.stripe.com; "
                "frame-src 'self' https://js.stripe.com https://hooks.stripe.com; "
                "base-uri 'self'; "
                "form-action 'self' https://checkout.stripe.com; "
                f"frame-ancestors {frame_ancestors}; "
                "upgrade-insecure-requests"
        )
        response.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(self), usb=(), interest-cohort=()",
        )
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        return response


class SocialLoginNetworkErrorMiddleware:
    """Send people back to sign-in when a provider (LinkedIn, Google) is unreachable.

    allauth fetches provider metadata on every login click, so an outbound
    network or DNS blip otherwise surfaces as a 500.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, requests.RequestException):
            return None
        if not request.path.startswith("/accounts/"):
            return None
        logger.warning("Social login provider unreachable at %s: %s", request.path, exception)
        messages.error(
            request,
            "We couldn't reach the sign-in provider just now. Please try again in a minute, "
            "or sign in with your email.",
        )
        return redirect("account_login")


class ReferralMiddleware:
    """Capture ?ref=CODE from any URL and store in session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ref_code = request.GET.get("ref")
        if ref_code and not request.user.is_authenticated:
            request.session["referral_code"] = ref_code
        return self.get_response(request)


class AcquisitionAttributionMiddleware:
    """Keep non-personal campaign labels through the signup flow."""

    SESSION_KEY = "acquisition_attribution"
    PARAMS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "source")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "GET":
            stored = request.session.get(self.SESSION_KEY, {})
            attribution = dict(stored) if isinstance(stored, dict) else {}
            incoming = {
                param: " ".join(request.GET.get(param, "").split())[:100]
                for param in self.PARAMS
            }
            incoming = {key: value for key, value in incoming.items() if value}
            # A source-bearing campaign URL is one coherent touch. Omitted
            # labels must not survive from a previous campaign, even when the
            # source happens to be the same. Internal source-only navigation
            # can still update the entry point without erasing the campaign.
            if incoming.get("utm_source"):
                attribution = incoming
            elif not any(key.startswith("utm_") for key in incoming):
                attribution.update(incoming)
            # Partial UTM URLs without a source cannot identify a new campaign;
            # do not splice them into the previously recorded one.
            if attribution != stored:
                request.session[self.SESSION_KEY] = attribution
        return self.get_response(request)


class CloudflareEdgeCacheMiddleware:
    """
    Set Cache-Control headers for Cloudflare edge caching.
    
    Uses s-maxage for shared caches (CDN) while keeping max-age lower
    for browser caches. This reduces origin load and improves global latency.
    """
    
    # URL patterns and their edge cache TTLs (in seconds)
    # More specific patterns first
    CACHE_RULES = [
        (r'^/$', 300),                              # Homepage: 5 min for anonymous users
        (r'^/domains/$', 600),                      # All domains: 10 min
        (r'^/domains/[^/]+/$', 600),                # Domain landing pages: 10 min
        (r'^/organizations/$', 300),                # Org list: 5 min
        (r'^/organizations/[^/]+/$', 300),          # Org detail: 5 min
        (r'^/resources/$', 3600),                   # Resources: 1 hour
        (r'^/about/$', 3600),                       # About: 1 hour
        (r'^/pricing/$', 3600),                     # Pricing: 1 hour
        (r'^/contact/$', 3600),                     # Contact: 1 hour
        (r'^/faq/$', 3600),                         # FAQ: 1 hour
        (r'^/privacy/$', 86400),                    # Privacy: 24 hours
        (r'^/terms/$', 86400),                      # Terms: 24 hours
        
        # Tools - cache longer (mostly static)
        (r'^/tools/[^/]+/$', 3600),                 # Tool pages: 1 hour
        (r'^/salary-calculator/$', 3600),           # Salary calc: 1 hour
        (r'^/cost-of-living/$', 3600),              # COL calc: 1 hour
        
        # Job pages - moderate caching
        (r'^/jobs/$', 60),                          # Job list (no filters): 1 min
        (r'^/jobs/category/[^/]+/$', 120),          # Category pages: 2 min
        (r'^/jobs/[^/]+/$', 300),                   # Job detail: 5 min
        (r'^/remote-[^/]+-jobs/$', 300),            # Role landing pages: 5 min
        (r'^/impact/[^/]+/$', 300),                 # Keyword landing pages: 5 min
        
        # Blog
        (r'^/blog/$', 600),                         # Blog list: 10 min
        (r'^/blog/[^/]+/$', 1800),                  # Blog post: 30 min
        
        # Sitemaps and feeds
        (r'^/sitemap.*\.xml$', 3600),               # Sitemaps: 1 hour
        (r'^/feeds/', 600),                         # RSS feeds: 10 min
        (r'^/robots\.txt$', 86400),                 # robots.txt: 24 hours
    ]
    
    # Never cache these paths (authenticated/dynamic/personalized)
    NO_CACHE_PATTERNS = [
        r'^/admin/',
        r'^/accounts/',
        r'^/account/',
        r'^/onboarding/',
        r'^/api/',
        r'^/dashboard/',
        r'^/jobs/post/',
        r'^/jobs/.*/save/',
        r'^/applications/',
        r'^/checkout/',
        r'^/webhook/',
        r'^/my-matches/',       # Personalized matches
        r'^/impact-profile/',   # User profile
        r'^/talent/',           # Talent directory (may show user-specific)
    ]
    
    def __init__(self, get_response):
        self.get_response = get_response
        # Pre-compile patterns
        self.cache_rules = [(re.compile(p), ttl) for p, ttl in self.CACHE_RULES]
        self.no_cache_patterns = [re.compile(p) for p in self.NO_CACHE_PATTERNS]
    
    def __call__(self, request):
        response = self.get_response(request)
        
        # Only cache safe read requests
        if request.method not in ('GET', 'HEAD'):
            return response
        
        # Don't cache for authenticated users
        user = getattr(request, 'user', None)
        if user and getattr(user, 'is_authenticated', False):
            # Keep a stricter "no-store" (private files such as CVs) rather than weakening it.
            if 'no-store' not in response.get('Cache-Control', ''):
                response['Cache-Control'] = 'private, no-cache'
            return response
        
        # Don't cache if response already has strong cache directive
        existing_cc = response.get('Cache-Control', '')
        if existing_cc and 'no-store' in existing_cc:
            return response
        
        # Don't cache error responses
        if response.status_code >= 400:
            return response
        
        path = request.path
        
        # Check no-cache patterns first
        for pattern in self.no_cache_patterns:
            if pattern.match(path):
                response['Cache-Control'] = 'private, no-cache'
                return response
        
        # Check if URL has query params (search/filter) - shorter cache
        has_query = bool(request.GET)
        
        # Find matching cache rule
        for pattern, edge_ttl in self.cache_rules:
            if pattern.match(path):
                # Reduce TTL for filtered/search pages
                if has_query and path.startswith('/jobs'):
                    edge_ttl = min(edge_ttl, 60)
                
                browser_ttl = min(edge_ttl, 60)
                response['Cache-Control'] = f'public, max-age={browser_ttl}, s-maxage={edge_ttl}'
                # CRITICAL: Include Cookie in Vary to prevent serving cached authenticated pages to wrong users
                patch_vary_headers(response, ('Accept-Encoding', 'Cookie'))
                return response
        
        # Default: don't set aggressive caching for unmatched paths
        return response


class ImpersonationReadOnlyMiddleware:
    """While staff view the site as another user, refuse anything but reads (and the way back)."""

    SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
    ALLOWED_PATHS = ("/admin/impersonate/stop/",)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        impersonating = bool(getattr(request, "session", None) and request.session.get("impersonator_id"))
        request.impersonating = impersonating
        if impersonating and request.method not in self.SAFE_METHODS and request.path not in self.ALLOWED_PATHS:
            from django.http import HttpResponse

            return HttpResponse(
                "Read-only: you're viewing the site as another user, so nothing can be saved. "
                "Use the bar at the top of the page to go back to your own account.",
                status=403, content_type="text/plain",
            )
        response = self.get_response(request)
        if impersonating:
            response["Cache-Control"] = "private, no-store"
        return response
class PerformanceMiddleware(MiddlewareMixin):
    """Record route-template timings without logging request paths or parameters."""

    def process_request(self, request):
        if getattr(settings, "PERFORMANCE_METRICS_ENABLED", True):
            request._performance_started = monotonic()

    def process_response(self, request, response):
        started = getattr(request, "_performance_started", None)
        if started is None:
            return response
        seconds = max(0, monotonic() - started)
        route = safe_route(getattr(getattr(request, "resolver_match", None), "route", None))
        cache = getattr(request, "_performance_cache_status", None) or getattr(response, "_performance_cache_status", None)
        if cache not in {"hit", "fresh", "stale"}:
            cache = "unknown"
        response["Server-Timing"] = "app;dur={:.1f}".format(seconds * 1000)
        sample_rate = getattr(settings, "PERFORMANCE_SAMPLE_RATE", 1.0)
        try:
            sample_rate = min(1.0, max(0.0, float(sample_rate)))
        except (TypeError, ValueError):
            sample_rate = 1.0
        if sample_rate >= 1 or random.random() < sample_rate:
            code = getattr(response, "status_code", 0)
            outcome = "ok" if 200 <= code < 400 else "error"
            emit_event({"kind": "request", "route": route, "seconds": round(seconds, 6),
                        "status": "{}xx".format(int(code) // 100) if code else "unknown",
                        "cache": cache, "outcome": outcome,
                        "sample_rate": sample_rate})
        return response
