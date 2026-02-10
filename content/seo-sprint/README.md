# SEO Sprint: Impact Career Resources Hub

## Overview

This directory contains draft content for a **Pillar-Cluster content hub** designed
to replace the generic blog section with a high-authority "Impact Career Resources"
section.  All files are Django templates ready for review before going live.

---

## Architecture: Pillar-Cluster Model

```
/resources/                              (existing resources page — becomes hub landing)
  /resources/climate-environment-careers/         [PILLAR]
      break-into-climate-tech-no-stem/            [CLUSTER]
      top-remote-climate-employers/               [CLUSTER]
      climate-career-switch-guide/                [CLUSTER]
  /resources/ai-safety-careers/                   [PILLAR]
      non-technical-ai-safety-roles/              [CLUSTER]
      alignment-research-pathway/                 [CLUSTER]
      ai-governance-fellowships/                  [CLUSTER]
  /resources/global-health-careers/               [PILLAR]
      remote-global-health-without-mph/           [CLUSTER]
      un-agency-remote-jobs-guide/                [CLUSTER]
      health-tech-impact-careers/                 [CLUSTER]
  /resources/humanitarian-ingo-careers/           [PILLAR]
      remote-ingo-job-no-field-experience/        [CLUSTER]
      un-consultancy-guide/                       [CLUSTER]
      mel-skills-humanitarian-career/             [CLUSTER]
  /resources/effective-altruism-careers/           [PILLAR]
      ea-operations-roles-guide/                  [CLUSTER]
      mid-career-switch-to-ea/                    [CLUSTER]
      ea-grantmaking-career-path/                 [CLUSTER]
```

**Total new pages: 20** (5 pillars + 15 clusters)

---

## Internal Linking Strategy

### Vertical links (parent ↔ child)
- Each **cluster post** links back to its **pillar page** via breadcrumb nav + in-text CTA
- Each **pillar page** links to all 3 of its **cluster posts** via a "Related reads" box
- The existing **resources page** (`/resources/`) should link to all 5 pillar pages

### Horizontal links (cross-pillar)
- Where relevant, cluster posts can link to pillars in adjacent verticals
  (e.g., the AI governance fellowships post can link to the EA pillar)
- All pages link to the job listing with the matching category filter
  (e.g., `/jobs/?category=climate-environment`)

### Sitewide links
- Add pillar pages to the **footer** under a new "Career Guides" column
- Add pillar pages to the **sitemap** in `jobs/sitemaps.py`
- Consider adding a "Guides" dropdown in the top nav linking to the 5 pillars

---

## SEO Metadata Checklist

| Page | Title (≤60 chars) | Meta Description (≤160 chars) | Schema |
|------|--------------------|-------------------------------|--------|
| **Climate Pillar** | Remote Climate Jobs: The Ultimate Career Guide (2026) | Land a remote climate job. Explore roles in renewable energy, conservation, and sustainability. Skills, salaries, and open positions inside. | BlogPosting, FAQPage, BreadcrumbList |
| **AI Safety Pillar** | Remote AI Safety Jobs: Career Guide for 2026 | Explore remote AI safety and governance careers. Learn the skills, orgs, and pathways to work on AI alignment, policy, and responsible AI. | BlogPosting, FAQPage, BreadcrumbList |
| **Global Health Pillar** | Remote Global Health Jobs: Career Guide 2026 | Find remote global health jobs in pandemic preparedness, public health, and medical research. Skills, orgs, and open roles inside. | BlogPosting, FAQPage, BreadcrumbList |
| **Humanitarian Pillar** | Remote INGO & Humanitarian Jobs: Career Guide | Land a remote job at an INGO or humanitarian org. Roles in disaster relief, refugee support, and development — no field experience required. | BlogPosting, FAQPage, BreadcrumbList |
| **EA Pillar** | Effective Altruism Jobs: Remote Career Guide | Find remote effective altruism jobs. Explore EA career paths in research, operations, grantmaking, and field-building. Open roles inside. | BlogPosting, FAQPage, BreadcrumbList |
| *Climate Cluster 1* | Break Into Climate Tech Without a STEM Degree | You don't need a science background to work in climate. Here are 7 in-demand climate roles for non-STEM professionals and how to land one. | BlogPosting, BreadcrumbList |
| *Climate Cluster 2* | 10 Orgs Hiring Remote Climate Pros in 2026 | Discover 10 leading organisations actively hiring remote climate professionals in 2026 — from startups to established NGOs. | BlogPosting, BreadcrumbList |
| *Climate Cluster 3* | Career Switch to Climate: A 90-Day Action Plan | Switch to a climate career in 90 days. Week-by-week plan covering skills, networking, portfolio building, and landing your first role. | BlogPosting, BreadcrumbList |
| *AI Cluster 1* | 5 Non-Technical AI Safety Roles You Can Apply For | AI safety isn't just for ML researchers. Explore 5 non-technical roles — from policy to operations — at top AI safety organisations. | BlogPosting, BreadcrumbList |
| *AI Cluster 2* | From Engineer to Alignment Researcher: A Guide | A step-by-step guide for software engineers who want to transition into AI alignment research. Programmes, skills, and resources inside. | BlogPosting, BreadcrumbList |
| *AI Cluster 3* | Best AI Governance Fellowships & How to Get In | The top AI governance fellowships for aspiring policy professionals. Application tips, deadlines, and what fellows actually do. | BlogPosting, BreadcrumbList |
| *Health Cluster 1* | Remote Global Health Jobs Without an MPH | No MPH? No problem. Discover remote global health roles in tech, comms, data, and operations that don't require a public health degree. | BlogPosting, BreadcrumbList |
| *Health Cluster 2* | UN Remote Jobs: A Guide for Health Workers | Navigate the UN job application system for remote health roles. Understand P-levels, consultancies, rosters, and how to write a winning UN CV. | BlogPosting, BreadcrumbList |
| *Health Cluster 3* | Health-Tech Startups Making Impact in 2026 | Meet the health-tech startups transforming global health in 2026. From AI diagnostics to drone delivery — and they're hiring remotely. | BlogPosting, BreadcrumbList |
| *INGO Cluster 1* | Land a Remote INGO Job Without Field Experience | No field experience? Here's how to break into remote INGO work through headquarters roles in grants, data, comms, and operations. | BlogPosting, BreadcrumbList |
| *INGO Cluster 2* | UN Remote Consultancies: Find and Win Them | A practical guide to finding, applying for, and winning remote UN consultancies. Daily rates, platforms, and proposal writing tips. | BlogPosting, BreadcrumbList |
| *INGO Cluster 3* | M&E Skills That Fast-Track Humanitarian Careers | Monitoring & evaluation skills are the fastest path into humanitarian work. Learn the tools, frameworks, and certifications employers want. | BlogPosting, BreadcrumbList |
| *EA Cluster 1* | Why EA Ops Roles Are the Most Underrated Path | EA operations roles are critically understaffed and highly impactful. Learn what they involve, what they pay, and how to land one. | BlogPosting, BreadcrumbList |
| *EA Cluster 2* | Switching to EA Mid-Career: What to Know | Considering a mid-career switch to effective altruism? Here's how to leverage your existing skills and find impactful EA roles. | BlogPosting, BreadcrumbList |
| *EA Cluster 3* | Build a Career in EA Grantmaking | EA grantmaking is one of the most impactful career paths. Learn how to become a programme officer at Open Philanthropy, GiveWell, and more. | BlogPosting, BreadcrumbList |

---

## Implementation Steps (Django)

### 1. Create a Blog/Article model (optional but recommended)

Instead of serving these as static templates, consider creating a lightweight
`Article` model:

```python
class Article(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    pillar = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL)
    body = models.TextField()  # HTML or Markdown
    meta_title = models.CharField(max_length=60)
    meta_description = models.CharField(max_length=160)
    published_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True)
```

This allows dynamic sitemap generation, admin editing, and programmatic linking
to related jobs.

### 2. URL patterns

```python
# In jobs/urls.py or a new content/urls.py
urlpatterns = [
    path('resources/<slug:pillar_slug>/', views.pillar_detail, name='pillar_detail'),
    path('resources/<slug:pillar_slug>/<slug:article_slug>/', views.article_detail, name='article_detail'),
]
```

### 3. Sitemap additions

Add an `ArticleSitemap` class to `jobs/sitemaps.py`:
- Priority: 0.7 for pillars, 0.6 for clusters
- Changefreq: weekly

### 4. Footer / navigation updates

Add a "Career Guides" section to the footer with links to the 5 pillar pages.

### 5. RSS feed

Extend the existing `LatestJobsFeed` pattern to create an `ArticleFeed` for
blog content.

---

## File Inventory

```
content/seo-sprint/
├── README.md                          ← this file
├── pillars/
│   ├── climate-environment.html       ← pillar: Climate & Environment
│   ├── ai-safety.html                 ← pillar: AI Safety & Governance
│   ├── global-health.html             ← pillar: Global Health
│   ├── humanitarian-ingo.html         ← pillar: Humanitarian & INGOs
│   └── effective-altruism.html        ← pillar: Effective Altruism
└── clusters/
    ├── climate-break-into-no-stem.html
    ├── climate-top-remote-employers.html
    ├── climate-career-switch-90-day.html
    ├── ai-safety-non-technical-roles.html
    ├── ai-safety-alignment-researcher-path.html
    ├── ai-safety-governance-fellowships.html
    ├── health-remote-without-mph.html
    ├── health-un-agency-guide.html
    ├── health-tech-startups-2026.html
    ├── ingo-no-field-experience.html
    ├── ingo-un-consultancy-guide.html
    ├── ingo-mel-skills-career.html
    ├── ea-operations-roles.html
    ├── ea-mid-career-switch.html
    └── ea-grantmaking-career.html
```
