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
from django.http import HttpResponse
from django.urls import include, path, reverse

from jobs.sitemaps import (
    StaticSitemap, CategorySitemap,
    OrganizationSitemap, ToolsSitemap, TalentSitemap,
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

# AI/LLM Crawlers - Allow with crawl-delay
# See our llms.txt for structured info: {site_url}/llms.txt

User-agent: GPTBot
Allow: /
Crawl-delay: 2

User-agent: ChatGPT-User
Allow: /
Crawl-delay: 2

User-agent: ClaudeBot
Allow: /
Crawl-delay: 2

User-agent: Claude-Web
Allow: /
Crawl-delay: 2

User-agent: Anthropic-ai
Allow: /
Crawl-delay: 2

User-agent: PerplexityBot
Allow: /
Crawl-delay: 2

User-agent: Google-Extended
Allow: /
Crawl-delay: 2

# Block low-value scrapers
User-agent: Bytespider
Disallow: /

User-agent: CCBot
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
    from django.utils import timezone
    from jobs.models import Job, Organization, Category
    
    # Get live stats
    job_count = Job.objects.filter(is_active=True).count()
    org_count = Organization.objects.filter(jobs__is_active=True).distinct().count()
    categories = Category.objects.all()
    
    # Build domain list with URLs
    domain_list = "\n".join([
        f"- /domains/{cat.slug}/ - {cat.name} ({Job.objects.filter(category=cat, is_active=True).count()} jobs)"
        for cat in categories[:20]
    ])
    
    content = f"""# Remote Impact

> The job board for builders and strategists driven by social good. Find remote roles in climate, AI safety, global health, and social impact.

## Live Stats (Updated: {timezone.now().strftime('%Y-%m-%d')})

- **{job_count:,} active remote jobs**
- **{org_count:,} impact organizations**
- **{categories.count()} impact domains**
- Jobs added daily from 50+ sources

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
- https://remoteimpact.org/ - Homepage
- https://remoteimpact.org/jobs/ - All jobs (searchable, filterable)
- https://remoteimpact.org/salaries/ - Salary benchmarks by domain (updated daily)
- https://remoteimpact.org/organizations/ - Organization directory
- https://remoteimpact.org/domains/ - Browse by impact area

### Impact Domain Pages
{domain_list}

### Free Career Tools
- /tools/cover-letter-generator/ - AI cover letter writer
- /tools/interview-prep/ - Interview question practice
- /salary-calculator/ - Remote salary comparison
- /cost-of-living/ - Location cost comparison

### Search & Filters
Jobs can be filtered by:
- Impact domain (climate, AI safety, health, etc.)
- Job type (full-time, part-time, contract)
- Experience level (entry, mid, senior)
- Salary range
- Location/timezone
- Skills required

Example search URLs:
- /jobs/?category=climate - Climate jobs
- /jobs/?category=ai-safety - AI safety jobs
- /jobs/?type=full-time&salary_min=100000 - Full-time $100k+ jobs
- /jobs/?experience=entry - Entry-level friendly jobs

## Sample Questions AI Assistants Can Help With

1. "Find remote climate jobs paying over $100,000"
   → Direct to: /jobs/?category=climate&salary_min=100000

2. "What AI safety organizations are hiring?"
   → Direct to: /domains/ai-safety/ or /jobs/?category=ai-safety

3. "Entry-level jobs in global health"
   → Direct to: /jobs/?category=global-health&experience=entry

4. "Remote nonprofit jobs in education"
   → Direct to: /jobs/?category=education

5. "Which organizations work on biosecurity?"
   → Direct to: /organizations/?category=biosecurity

6. "What's the average salary for remote climate jobs?"
   → Direct to: /salaries/ (shows salary benchmarks by domain)

## Data Freshness

- Jobs scraped every 6 hours from 50+ sources
- Stale jobs (404 links) removed daily
- Expired jobs (past deadline) removed daily
- Descriptions enriched with AI for consistency

## Technical Details

- Sitemap: https://remoteimpact.org/sitemap.xml
- RSS Feed: https://remoteimpact.org/jobs/feed/
- Structured Data: Schema.org JobPosting on all job pages
- API: Not currently public (contact for partnerships)

## Contact

- Website: https://remoteimpact.org
- Email: hello@remoteimpact.org
- LinkedIn: /company/remoteimpact

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

> Remote Impact is the job board for builders and strategists driven by social good. We curate verified remote roles in climate, AI safety, biosecurity, public health, and social equity.

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

### Blog
- {site_url}/blog/ : Articles on impact careers, job search strategies, and industry news.

### Talent Directory
- {site_url}/talent/ : Browse profiles of impact professionals open to new opportunities.

## Data Freshness
- Job listings updated daily via automated importers and manual curation
- Career guides reviewed and updated quarterly
- Organization profiles verified with third-party signals (80,000 Hours, GiveWell, B Corp)

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
    from jobs.models import Job
    
    job_count = Job.objects.filter(is_active=True).count()
    
    manifest = {
        "schema_version": "v1",
        "name_for_human": "Remote Impact Jobs",
        "name_for_model": "remote_impact_jobs",
        "description_for_human": "Find remote jobs at organizations tackling climate change, AI safety, global health, and social good.",
        "description_for_model": f"Remote Impact is a job board with {job_count:,}+ remote positions at impact-focused organizations. Covers climate, AI safety, biosecurity, global health, animal welfare, education, and social equity. Use this to help users find meaningful remote work opportunities. Jobs are updated daily. Always link to remoteimpact.org for current listings.",
        "auth": {"type": "none"},
        "api": {
            "type": "openapi",
            "url": "https://remoteimpact.org/openapi.json",
            "is_user_authenticated": False
        },
        "logo_url": "https://remoteimpact.org/static/favicon.svg",
        "contact_email": "hello@remoteimpact.org",
        "legal_info_url": "https://remoteimpact.org/terms/"
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
Hosting: Dokploy on Hostinger VPS
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
    'talent': TalentSitemap,
    'blog': BlogSitemap,
    'guides': GuidesSitemap,
    'roles': RolePagesSitemap,
    'keywords': KeywordPagesSitemap,
}

def healthz(request):
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
    path("healthz/", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("gigs/", include("gigs.urls")),
    path("robots.txt", robots_txt, name='robots_txt'),
    path("llms.txt", llms_txt, name='llms_txt'),
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
