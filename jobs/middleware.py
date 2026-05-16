import re


class SecurityHeadersMiddleware:
    """Add conservative browser security headers site-wide."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        response.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
                "https://www.googletagmanager.com https://www.google-analytics.com "
                "https://js.stripe.com https://www.gstatic.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com data:; "
                "img-src 'self' data: https:; "
                "connect-src 'self' https://www.google-analytics.com https://region1.google-analytics.com https://api.stripe.com; "
                "frame-src 'self' https://js.stripe.com https://hooks.stripe.com; "
                "base-uri 'self'; "
                "form-action 'self' https://checkout.stripe.com; "
                "frame-ancestors 'none'; "
                "upgrade-insecure-requests"
            ),
        )
        response.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(self), usb=(), interest-cohort=()",
        )
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        return response


class ReferralMiddleware:
    """Capture ?ref=CODE from any URL and store in session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ref_code = request.GET.get("ref")
        if ref_code and not request.user.is_authenticated:
            request.session["referral_code"] = ref_code
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
                response['Vary'] = 'Accept-Encoding, Cookie'
                return response
        
        # Default: don't set aggressive caching for unmatched paths
        return response
