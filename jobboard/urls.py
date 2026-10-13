"""
URL configuration for jobboard project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from math import ceil
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse
from django.urls import include, path, reverse
from django.views.generic import RedirectView

from jobs.views import admin_analytics, impersonate
from jobs.sitemaps import (
    StaticSitemap, CategorySitemap,
    OrganizationSitemap, ToolsSitemap, TalentSitemap, RegionSitemap,
    GuidesSitemap, RolePagesSitemap, KeywordPagesSitemap,
    visible_jobs,
)
from blog.sitemaps import BlogSitemap


def robots_txt(request):
    from django.conf import settings
    site_url = getattr(settings, 'SITE_URL', 'https://remoteimpact.org')
    content = f"""# Remote Impact - Remote Jobs for Social Good
# https://remoteimpact.org

User-agent: *
Allow: /
Disallow: /admin/
Disallow: /accounts/
Disallow: /api/
Disallow: /checkout/
Disallow: /jobs/*/feature/
Disallow: /jobs/*/go/

# AI/LLM Crawlers - Allow with crawl-delay
# See our llms.txt for structured info: {site_url}/llms.txt

User-agent: GPTBot
Allow: /
Disallow: /admin/
Disallow: /accounts/
Disallow: /api/
Disallow: /checkout/
Disallow: /jobs/*/feature/
Disallow: /jobs/*/go/
Crawl-delay: 2

User-agent: ChatGPT-User
Allow: /
Disallow: /admin/
Disallow: /accounts/
Disallow: /api/
Disallow: /checkout/
Disallow: /jobs/*/feature/
Disallow: /jobs/*/go/
Crawl-delay: 2

User-agent: ClaudeBot
Allow: /
Disallow: /admin/
Disallow: /accounts/
Disallow: /api/
Disallow: /checkout/
Disallow: /jobs/*/feature/
Disallow: /jobs/*/go/
Crawl-delay: 2

User-agent: Claude-Web
Allow: /
Disallow: /admin/
Disallow: /accounts/
Disallow: /api/
Disallow: /checkout/
Disallow: /jobs/*/feature/
Disallow: /jobs/*/go/
Crawl-delay: 2

User-agent: Anthropic-ai
Allow: /
Disallow: /admin/
Disallow: /accounts/
Disallow: /api/
Disallow: /checkout/
Disallow: /jobs/*/feature/
Disallow: /jobs/*/go/
Crawl-delay: 2

User-agent: PerplexityBot
Allow: /
Disallow: /admin/
Disallow: /accounts/
Disallow: /api/
Disallow: /checkout/
Disallow: /jobs/*/feature/
Disallow: /jobs/*/go/
Crawl-delay: 2

User-agent: Google-Extended
Allow: /
Disallow: /admin/
Disallow: /accounts/
Disallow: /api/
Disallow: /checkout/
Disallow: /jobs/*/feature/
Disallow: /jobs/*/go/
Crawl-delay: 2

# AI search and training crawlers that cite or learn from public pages: allowed (they follow the
# default rules above). Common Crawl (CCBot) feeds many AI models; Amazonbot powers Alexa/Rufus answers.

# Block low-value scrapers
User-agent: Bytespider
Disallow: /

User-agent: DataForSeoBot
Disallow: /

User-agent: SemrushBot
Disallow: /

User-agent: AhrefsBot
Disallow: /

User-agent: PetalBot
Disallow: /

# Sitemaps
Sitemap: {site_url}/sitemap.xml

# AI assistant instructions
# llms.txt: {site_url}/llms.txt
# ai-plugin.json: {site_url}/.well-known/ai-plugin.json
"""
    return HttpResponse(content, content_type="text/plain")


def indexnow_key(request):
    """IndexNow API key verification file."""
    return HttpResponse("db25ddbb333d413289a18c8820c32ca4", content_type="text/plain")


def llms_txt(request):
    """LLMs.txt - Help AI assistants understand Remote Impact."""
    from django.conf import settings
    from django.db.models import Count
    from django.utils import timezone
    from jobs.models import Organization, Category
    from jobs.services.job_service import JobService

    site_url = getattr(settings, 'SITE_URL', 'https://remoteimpact.org').rstrip('/')
    available_jobs = JobService.get_available_jobs()
    
    # Get live stats (distinct openings, matching the job list)
    job_count = JobService.count_openings()
    org_count = Organization.objects.filter(jobs__is_active=True).distinct().count()
    category_counts = dict(
        available_jobs.values('category_id')
        .annotate(total=Count('id'))
        .values_list('category_id', 'total')
    )
    categories = Category.objects.order_by('name')
    
    # Build domain list with URLs
    domain_list = "\n".join([
        f"- {site_url}/jobs/category/{cat.slug}/ - {cat.name} ({category_counts.get(cat.id, 0)} jobs)"
        for cat in categories[:20]
        if category_counts.get(cat.id, 0)
    ]) or "- No categorized jobs currently available"

    source_count = available_jobs.values('source').distinct().count()
    
    content = f"""# Remote Impact

> The job board for builders and strategists driven by social good. Find remote roles in climate, AI safety, global health, and social impact.

## Live Stats (Updated: {timezone.now().strftime('%Y-%m-%d')})

- **{job_count:,} active remote jobs**
- **{org_count:,} impact organizations**
- **{categories.count()} impact domains**
- Jobs added daily from {source_count} tracked source families

## What We Are

Remote Impact is a curated job board exclusively for **remote positions at impact-focused organizations**. We cover:
- Climate & clean energy
- AI safety & governance  
- Global health & biosecurity
- Animal welfare
- Education & social equity
- Humanitarian aid
- Effective altruism

100% free for job seekers. No account required to browse.

## Site Structure

### Main Pages
- {site_url}/ - Homepage
- {site_url}/jobs/ - All jobs (searchable, filterable)
- {site_url}/salaries/ - Salary benchmarks by domain (updated daily)
- {site_url}/organizations/ - Organization directory
- {site_url}/domains/ - Browse by impact area
- {site_url}/how-we-check/ - How every listing is verified daily against the employer's own job board (closed, office-based and reseller listings are removed)
- {site_url}/alerts/ - AI Job Scout: describe the role you want in plain words and get matching current roles by email

### Impact Domain Pages
{domain_list}

### Free Career Tools
- /tools/cover-letter-generator/ - AI cover letter writer
- /tools/interview-prep/ - Interview question practice
- /tools/salary-to-hourly-calculator/ - Remote salary comparison
- /tools/cost-of-living-comparison/ - Location cost comparison

### Pricing & Alerts
- {site_url}/pricing/ - Free one-time AI Job Scout; Pro ongoing alerts at $10/month (7-day free trial) or $24 for a 3-month pass
- {site_url}/employers/ - For employers: post a featured remote role ($99 for 30 days) or feature an existing listing ($49), with view and apply-click stats
- {site_url}/alerts/ - Describe a target role in plain English and receive current matches

### Search & Filters
Jobs can be filtered by:
- Impact domain (climate, AI safety, health, etc.)
- Job type (full-time, part-time, contract)
- Experience level (entry, mid, senior)
- Salary range
- Location/timezone
- Skills required

Example search URLs:
- /jobs/?category=climate-environment - Climate jobs
- /jobs/?category=ai-safety - AI safety jobs
- /jobs/?type=full-time&salary_min=100000 - Full-time $100k+ jobs
- /jobs/?experience=entry - Entry-level friendly jobs

## Sample Questions AI Assistants Can Help With

1. "Find remote climate jobs paying over $100,000"
   → Direct to: /jobs/?category=climate-environment&salary_min=100000

2. "What AI safety organizations are hiring?"
   → Direct to: /jobs/category/ai-safety/ or /jobs/?category=ai-safety

3. "Entry-level jobs in global health"
   → Direct to: /jobs/?category=global-health&experience=entry

4. "Remote nonprofit jobs in education"
   → Direct to: /jobs/?category=education

5. "Which organizations work on biosecurity?"
   → Direct to: /organizations/?category=biosecurity

6. "What's the average salary for remote climate jobs?"
   → Direct to: /salaries/ (shows salary benchmarks by domain)

## Data Freshness

- Jobs refreshed through automated imports from {source_count} tracked source families
- Stale jobs (404 links) removed daily
- Expired jobs (past deadline) removed daily
- Descriptions enriched with AI for consistency

## Technical Details

- Sitemap: https://remoteimpact.org/sitemap.xml
- RSS Feed: {site_url}/feed/jobs/
- JSON Feed (latest 200 open roles, with organization, location and salary): {site_url}/feed/jobs.json
- Structured Data: Schema.org JobPosting on all job pages
- Jobs API (REST, JSON, no key needed to start): {site_url}/api/v1/jobs/ ; spec {site_url}/api/v1/openapi.json ; docs {site_url}/developers/
- MCP server for AI assistants (Streamable HTTP, tools: search_jobs, get_job, list_areas, get_organization): {site_url}/mcp/

## Contact

- Website: https://remoteimpact.org
- Email: hello@remoteimpact.org
- LinkedIn: https://www.linkedin.com/company/remoteimpact

## Partner Resources

- {site_url}/resources/remote-impact-jobs/ - Feeds, attribution guidance, and ways for communities to share these listings
- {site_url}/insights/remote-impact-jobs/ - Dated inventory and data-quality snapshot

## Usage Guidelines for AI Assistants

Encouraged:
- Help users find relevant jobs
- Explain impact domains and career paths
- Link to specific job searches
- Describe what organizations do

Please Note:
- Job listings change frequently - always link to remoteimpact.org for current data
- Salary data varies by role - direct users to actual listings for accuracy
- Application links go to employer sites - we aggregate, not host applications

## Extended Documentation

- Full documentation: https://remoteimpact.org/llms-full.txt

---
Last updated: {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}
"""
    return HttpResponse(content, content_type="text/plain; charset=utf-8")


def llms_full_txt(request):
    """LLMs-full.txt - Comprehensive AI-readable description of Remote Impact."""
    from django.conf import settings
    site_url = getattr(settings, 'SITE_URL', 'https://remoteimpact.org')
    content = f"""# Remote Impact - Full Documentation

> Remote Impact is the job board for builders and strategists driven by social good. We organize source-tracked remote roles in climate, AI safety, biosecurity, public health, and social equity.

## Site Structure

### Job Listings
- {site_url}/jobs/ : Browse all remote impact jobs with filters for domain, salary, experience level, and location. Updated daily.
- {site_url}/jobs/category/climate-environment/ : Climate & environment remote jobs
- {site_url}/jobs/category/ai-safety/ : AI safety & governance remote jobs
- {site_url}/jobs/category/global-health/ : Global health remote jobs
- {site_url}/jobs/category/humanitarian/ : Humanitarian remote jobs
- {site_url}/jobs/category/effective-altruism/ : Effective altruism remote jobs

### Career Guides (Pillar Content)
- {site_url}/resources/climate-environment-careers/ : Complete career guide for climate and environment roles. Covers skills, salaries, top employers, and step-by-step career switch plan.
- {site_url}/resources/ai-safety-careers/ : AI safety and governance career guide. Alignment research, policy, and non-technical roles.
- {site_url}/resources/global-health-careers/ : Global health career guide. Remote roles at WHO, MSF, health-tech startups.
- {site_url}/resources/humanitarian-ingo-careers/ : Humanitarian and INGO career guide. UN consultancies, M&E, remote INGO roles.
- {site_url}/resources/effective-altruism-careers/ : Effective altruism career guide. Operations, grantmaking, and research roles.

### Free Career Tools
- {site_url}/tools/ : Index of 15 free career tools
- {site_url}/tools/cover-letter-generator/ : AI cover letter generator
- {site_url}/tools/interview-prep/ : AI interview preparation
- {site_url}/tools/salary-negotiation-script/ : Salary negotiation scripts
- {site_url}/tools/salary-to-hourly-calculator/ : Salary to hourly converter
- {site_url}/tools/freelance-rate-calculator/ : Freelance rate calculator
- {site_url}/tools/cost-of-living-comparison/ : Cost of living comparison tool
- {site_url}/tools/pto-calculator/ : PTO calculator
- {site_url}/tools/pay-raise-calculator/ : Pay raise calculator
- {site_url}/tools/word-counter/ : Word counter
- {site_url}/tools/pomodoro-timer/ : Pomodoro timer
- {site_url}/tools/skills-gap-analyzer/ : Skills gap analyzer
- {site_url}/tools/thank-you-email-generator/ : Thank you email generator
- {site_url}/tools/job-description-generator/ : Job description generator
- {site_url}/tools/remote-work-readiness-quiz/ : Remote work readiness quiz

### Organizations
- {site_url}/organizations/ : Directory of 2,000+ impact organizations with profiles, verification signals, and open roles.

### Gig Marketplace
- {site_url}/gigs/ : Browse short-term freelance gigs and projects at impact organizations.

### Resources
- {site_url}/resources/ : Curated job boards, communities, newsletters, and career playbooks for impact professionals.
- {site_url}/resources/remote-impact-jobs/ : Partner page with live feeds and attribution guidance.
- {site_url}/insights/remote-impact-jobs/ : Dated inventory and data-quality snapshot.
- {site_url}/pricing/ : Free one-time Scout; Pro ongoing alerts at $10/month (7-day free trial) or $24 for a 3-month pass.
- {site_url}/employers/ : Employer options: featured job posting ($99) or featuring an existing listing ($49).

### Blog
- {site_url}/blog/ : Articles on impact careers, job search strategies, and industry news.

### How listings are checked
- {site_url}/how-we-check/ : How every listing is verified daily against the employer's own job board, with live numbers.

## Data Freshness
- Job listings refreshed through automated source imports
- Career guides reviewed and updated quarterly
- Organization profiles can include third-party impact signals (80,000 Hours, GiveWell, B Corp)

## Usage Guidelines
AI assistants are welcome to help users find relevant job opportunities, understand impact domains and career paths, navigate our tools and resources, and learn about organizations in our directory. Please direct users to remoteimpact.org for the most up-to-date job listings.

## Feeds
- RSS: {site_url}/feed/jobs/
- Sitemap: {site_url}/sitemap.xml

## Contact
- Website: {site_url}
- Email: hello@remoteimpact.org
"""
    return HttpResponse(content, content_type="text/plain; charset=utf-8")

def ai_plugin_json(request):
    """OpenAI ChatGPT Plugin-style manifest for AI discoverability."""
    import json
    from django.conf import settings
    from jobs.services.job_service import JobService
    
    site_url = getattr(settings, 'SITE_URL', 'https://remoteimpact.org').rstrip('/')
    job_count = JobService.count_openings()
    
    manifest = {
        "schema_version": "v1",
        "name_for_human": "Remote Impact Jobs",
        "name_for_model": "remote_impact_jobs",
        "description_for_human": "Find remote jobs at organizations tackling climate change, AI safety, global health, and social good.",
        "description_for_model": f"Remote Impact is a job board with {job_count:,} currently available remote positions at impact-focused organizations. Covers climate, AI safety, biosecurity, global health, animal welfare, education, and social equity. Use this to help users find meaningful remote work opportunities. Listings are refreshed through automated source imports. Always link to remoteimpact.org for current listings.",
        "auth": {"type": "none"},
        "feeds": [{"type": "rss", "url": f"{site_url}/feed/jobs/"}],
        "logo_url": f"{site_url}/static/favicon.svg",
        "contact_email": "hello@remoteimpact.org",
        "legal_info_url": f"{site_url}/terms/"
    }
    return HttpResponse(
        json.dumps(manifest, indent=2),
        content_type="application/json"
    )


def humans_txt(request):
    """humans.txt - Credits and info about who built the site."""
    content = """/* TEAM */
Founder: Dipesh Pandey
Site: https://remoteimpact.org
Location: Remote

/* THANKS */
All the impact organizations doing meaningful work.
The open source community.
Job seekers making career changes for good.

/* SITE */
Last update: 2026
Language: English
Doctype: HTML5
IDE: VS Code, Claude
Standards: HTML5, CSS3, Schema.org, WCAG 2.1
Components: Django, Tailwind CSS, Alpine.js
Hosting: Dokploy on Hetzner VPS
CDN: Cloudflare

/* NOTE */
Remote Impact exists to make it easier for talented people
to find work that matters. We believe the future of work
is meaningful, and we're building the infrastructure to
make that future accessible to everyone.
"""
    return HttpResponse(content, content_type="text/plain; charset=utf-8")


def security_txt(request):
    """security.txt - Security contact information (RFC 9116)."""
    content = """# Security Policy for Remote Impact
# https://remoteimpact.org

Contact: mailto:security@remoteimpact.org
Contact: mailto:hello@remoteimpact.org
Expires: 2027-12-31T23:59:59.000Z
Preferred-Languages: en

# We appreciate responsible disclosure of security vulnerabilities.
# Please allow us reasonable time to address issues before public disclosure.
"""
    return HttpResponse(content, content_type="text/plain; charset=utf-8")


sitemaps = {
    'static': StaticSitemap,
    'categories': CategorySitemap,
    'organizations': OrganizationSitemap,
    'tools': ToolsSitemap,
    'blog': BlogSitemap,
    'guides': GuidesSitemap,
    'roles': RolePagesSitemap,
    'keywords': KeywordPagesSitemap,
    'regions': RegionSitemap,
}

def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        cache_key = "healthz:cache"
        cache.set(cache_key, "ok", 10)
        if cache.get(cache_key) != "ok":
            raise RuntimeError("cache read/write failed")
    except Exception:
        return HttpResponse("unhealthy", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")


JOB_SITEMAP_PAGE_SIZE = 2000


def _xml_response(content: str) -> HttpResponse:
    response = HttpResponse(content, content_type="application/xml")
    response["Cache-Control"] = "public, max-age=60, s-maxage=3600"
    return response


def _job_sitemap_page_count() -> int:
    return max(1, ceil(visible_jobs().count() / JOB_SITEMAP_PAGE_SIZE))


def sitemap_index(request):
    """Fast sitemap index that avoids expensive lastmod queries on every fetch."""
    cache_key = "seo:sitemap:index:v2"
    cached = cache.get(cache_key)
    if cached:
        return _xml_response(cached)

    site_url = getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")
    sections = [
        f"  <sitemap><loc>{site_url}/sitemap-jobs-{page}.xml</loc></sitemap>"
        for page in range(1, _job_sitemap_page_count() + 1)
    ]
    sections.extend(
        f"  <sitemap><loc>{site_url}/sitemap-{section}.xml</loc></sitemap>"
        for section in sitemaps
    )
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(sections)}
</sitemapindex>
"""
    cache.set(cache_key, content, 3600)
    return _xml_response(content)


def job_sitemap_index(request):
    """Compatibility index for the previous /sitemap-jobs.xml endpoint."""
    site_url = getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")
    sections = "\n".join(
        f"  <sitemap><loc>{site_url}/sitemap-jobs-{page}.xml</loc></sitemap>"
        for page in range(1, _job_sitemap_page_count() + 1)
    )
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{sections}
</sitemapindex>
"""
    return _xml_response(content)


def job_sitemap_page(request, page: int):
    """Small, cacheable job sitemap shards for faster Googlebot fetches."""
    if page < 1:
        return HttpResponse(status=404)

    cache_key = f"seo:sitemap:jobs:{page}:v2"
    cached = cache.get(cache_key)
    if cached:
        return _xml_response(cached)

    offset = (page - 1) * JOB_SITEMAP_PAGE_SIZE
    jobs = list(
        visible_jobs()
        .only("slug", "updated_at", "posted_at")
        .order_by("-posted_at", "-id")[offset: offset + JOB_SITEMAP_PAGE_SIZE]
    )
    if not jobs:
        return HttpResponse(status=404)

    site_url = getattr(settings, "SITE_URL", "https://remoteimpact.org").rstrip("/")
    url_rows = []
    for job in jobs:
        loc = f"{site_url}{reverse('jobs:job_detail', kwargs={'slug': job.slug})}"
        lastmod = (job.updated_at or job.posted_at).date().isoformat()
        url_rows.append(
            "  <url>"
            f"<loc>{xml_escape(loc)}</loc>"
            f"<lastmod>{lastmod}</lastmod>"
            "<changefreq>daily</changefreq>"
            "<priority>0.8</priority>"
            "</url>"
        )

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(url_rows)}
</urlset>
"""
    cache.set(cache_key, content, 3600)
    return _xml_response(content)


urlpatterns = [
    # Browsers and crawlers request these conventional icon paths even when the
    # SVG favicon is declared in the document head.
    path(
        "favicon.ico",
        RedirectView.as_view(url="/static/favicon.svg", permanent=True),
        name="favicon",
    ),
    path(
        "apple-touch-icon.png",
        RedirectView.as_view(url="/static/favicon.svg", permanent=True),
        name="apple_touch_icon",
    ),
    path(
        "apple-touch-icon-precomposed.png",
        RedirectView.as_view(url="/static/favicon.svg", permanent=True),
        name="apple_touch_icon_precomposed",
    ),
    path("healthz/", healthz, name="healthz"),
    path("admin/analytics/", admin.site.admin_view(admin_analytics.analytics), name="admin_analytics"),
    path("admin/outreach/", admin.site.admin_view(admin_analytics.outreach), name="admin_outreach"),
    path("admin/outreach/<int:org_id>/", admin.site.admin_view(admin_analytics.outreach_update), name="admin_outreach_update"),
    path("admin/impersonate/", admin.site.admin_view(impersonate.impersonate_search), name="admin_impersonate"),
    path("admin/impersonate/<int:user_id>/start/", admin.site.admin_view(impersonate.impersonate_start), name="admin_impersonate_start"),
    path("admin/impersonate/stop/", impersonate.impersonate_stop, name="admin_impersonate_stop"),
    path("admin/", admin.site.urls),
    # A few historical clients appended a literal null path segment to these
    # auth pages. Keep the canonical destination instead of a repeat 404.
    path(
        "accounts/login/null",
        RedirectView.as_view(url="/accounts/login/", permanent=True),
        name="login_null_redirect",
    ),
    path(
        "accounts/signup/null",
        RedirectView.as_view(url="/accounts/signup/", permanent=True),
        name="signup_null_redirect",
    ),
    path("accounts/", include("allauth.urls")),
    path("gigs/", include("gigs.urls")),
    path("robots.txt", robots_txt, name='robots_txt'),
    path("llms.txt", llms_txt, name='llms_txt'),
    path(f"{settings.INDEXNOW_KEY}.txt", lambda request: HttpResponse(settings.INDEXNOW_KEY, content_type="text/plain"), name="indexnow_key"),
    path("llms-full.txt", llms_full_txt, name='llms_full_txt'),
    path("humans.txt", humans_txt, name='humans_txt'),
    path(".well-known/ai-plugin.json", ai_plugin_json, name='ai_plugin'),
    path(".well-known/security.txt", security_txt, name='security_txt'),
    path("security.txt", security_txt, name='security_txt_root'),
    path("db25ddbb333d413289a18c8820c32ca4.txt", indexnow_key, name='indexnow_key'),
    path("sitemap.xml", sitemap_index, name='sitemap'),
    path("sitemap-jobs.xml", job_sitemap_index, name='job_sitemap_index'),
    path("sitemap-jobs-<int:page>.xml", job_sitemap_page, name='job_sitemap_page'),
    path(
        "sitemap-<section>.xml",
        sitemap,
        {'sitemaps': sitemaps},
        name='django.contrib.sitemaps.views.sitemap',
    ),
    path("blog/", include("blog.urls")),
    path("", include("jobs.urls")),
]

# Serve media files (in production, use nginx/CDN instead)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
