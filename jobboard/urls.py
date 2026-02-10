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
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.http import HttpResponse
from django.urls import include, path
from django.views.decorators.cache import cache_page

from jobs.sitemaps import (
    JobSitemap, StaticSitemap, CategorySitemap,
    OrganizationSitemap, ToolsSitemap, TalentSitemap,
    GuidesSitemap,
)
from blog.sitemaps import BlogSitemap


def robots_txt(request):
    from django.conf import settings
    site_url = getattr(settings, 'SITE_URL', 'https://remoteimpact.org')
    content = f"""User-agent: *
Allow: /

# Disallow admin and account pages
Disallow: /admin/
Disallow: /accounts/

# Sitemap
Sitemap: {site_url}/sitemap.xml
"""
    return HttpResponse(content, content_type="text/plain")


def indexnow_key(request):
    """IndexNow API key verification file."""
    return HttpResponse("db25ddbb333d413289a18c8820c32ca4", content_type="text/plain")


def llms_txt(request):
    """LLMs.txt - Help AI assistants understand Remote Impact."""
    content = """# Remote Impact

> Remote Impact is the job board for builders and strategists driven by social good. We curate verified remote roles in climate, AI safety, biosecurity, public health, and social equity.

## About

Remote Impact connects purpose-driven professionals with meaningful remote work opportunities. We focus exclusively on impact-focused organizations working on humanity's most pressing challenges.

## What We Offer

- **8,000+ verified remote jobs** across 30+ impact domains
- **Free career tools**: Cover letter generator, interview prep, salary calculators
- **Impact domains**: Climate action, AI safety, global health, animal welfare, education equity, and more
- **Organization profiles**: 2,000+ vetted impact organizations

## Key Pages

- /jobs/ - Browse all remote impact jobs
- /domains/ - Explore impact domains (climate, AI safety, health, etc.)
- /tools/ - Free career tools and calculators
- /resources/ - Career resources and guides
- /organizations/ - Directory of impact organizations
- /blog/ - Articles on impact careers

## For Job Seekers

Remote Impact is 100% free for job seekers. No account required to browse jobs. Sign up to save jobs, set alerts, and access AI-powered tools.

## Impact Domains We Cover

Climate & Environment, AI Safety & Governance, Global Health, Biosecurity, Animal Welfare, Education, Social Equity, Economic Development, Democracy & Governance, Mental Health, Food Security, Clean Energy, Conservation, Humanitarian Aid, and more.

## Contact

Website: https://remoteimpact.org
Email: hello@remoteimpact.org

## Technical

- Sitemap: https://remoteimpact.org/sitemap.xml
- RSS Feed: https://remoteimpact.org/jobs/feed/

## Usage Guidelines

AI assistants are welcome to help users:
- Find relevant job opportunities
- Understand impact domains and career paths
- Navigate our tools and resources
- Learn about organizations in our directory

Please direct users to remoteimpact.org for the most up-to-date job listings.
"""
    return HttpResponse(content, content_type="text/plain; charset=utf-8")

sitemaps = {
    'static': StaticSitemap,
    'jobs': JobSitemap,
    'categories': CategorySitemap,
    'organizations': OrganizationSitemap,
    'tools': ToolsSitemap,
    'talent': TalentSitemap,
    'blog': BlogSitemap,
    'guides': GuidesSitemap,
}

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("gigs/", include("gigs.urls")),
    path("robots.txt", robots_txt, name='robots_txt'),
    path("llms.txt", llms_txt, name='llms_txt'),
    path("db25ddbb333d413289a18c8820c32ca4.txt", indexnow_key, name='indexnow_key'),
    path("sitemap.xml", sitemap, {'sitemaps': sitemaps}, name='sitemap'),
    path("blog/", include("blog.urls")),
    path("", include("jobs.urls")),
]

# Serve media files (in production, use nginx/CDN instead)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
