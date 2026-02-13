import re


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
        # Static-ish pages - cache longer at edge
        (r'^/$', 300),                              # Homepage: 5 min
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
        
        # Blog
        (r'^/blog/$', 600),                         # Blog list: 10 min
        (r'^/blog/[^/]+/$', 1800),                  # Blog post: 30 min
        
        # Sitemaps and feeds
        (r'^/sitemap.*\.xml$', 3600),               # Sitemaps: 1 hour
        (r'^/feeds/', 600),                         # RSS feeds: 10 min
        (r'^/robots\.txt$', 86400),                 # robots.txt: 24 hours
    ]
    
    # Never cache these paths (authenticated/dynamic)
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
    ]
    
    def __init__(self, get_response):
        self.get_response = get_response
        # Pre-compile patterns
        self.cache_rules = [(re.compile(p), ttl) for p, ttl in self.CACHE_RULES]
        self.no_cache_patterns = [re.compile(p) for p in self.NO_CACHE_PATTERNS]
    
    def __call__(self, request):
        response = self.get_response(request)
        
        # Debug: Always set a header to verify middleware runs
        response['X-Edge-Cache-Middleware'] = 'active'
        
        # Only cache GET requests
        if request.method != 'GET':
            return response
        
        # Don't cache for authenticated users
        # Note: Check if user attr exists and is authenticated
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False):
            response['Cache-Control'] = 'private, no-cache'
            return response
        
        # Don't cache if response already has strong cache directive
        if response.get('Cache-Control') and 'no-store' in response.get('Cache-Control', ''):
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
                    edge_ttl = min(edge_ttl, 60)  # Max 1 min for filtered results
                
                # s-maxage = CDN cache time, max-age = browser cache time
                browser_ttl = min(edge_ttl, 60)  # Browser cache max 1 min
                response['Cache-Control'] = f'public, max-age={browser_ttl}, s-maxage={edge_ttl}'
                response['Vary'] = 'Accept-Encoding'
                return response
        
        # Default: don't set aggressive caching for unmatched paths
        return response
