"""Read-only production audit. Run through manage.py shell, never the email sender."""

import json
import re
import time
from collections import Counter
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.db.models import Count, F, Q
from django.db.models.functions import Coalesce
from django.http import QueryDict
from django.test import RequestFactory
from django.utils import timezone

from jobs.models import AssistantSubscription, Job, JobAlert, SeekerProfile
from jobs.services.job_scout_service import JobScoutService
from jobs.services.job_service import JobService
from jobs.views.jobs import JobListView


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.h2 = False
        self.slugs = []
        self.next_url = None
        self.next_link = False
        self.href = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h2":
            self.h2 = True
        if tag == "a":
            self.href = attrs.get("href", "")
            if self.h2 and re.fullmatch(r"/jobs/[^/]+/", self.href):
                self.slugs.append(self.href.split("/")[2])

    def handle_endtag(self, tag):
        if tag == "h2":
            self.h2 = False
        if tag == "a":
            self.href = None

    def handle_data(self, value):
        if self.href and value.strip() == "Next":
            self.next_url = self.href


def run():
    report = {"checked_at": timezone.now().isoformat(), "checks": [], "scouts": []}
    now = timezone.now()
    public = JobService.get_available_jobs()
    active = Job.objects.filter(is_active=True)

    def check(name, ok, **details):
        report["checks"].append({"name": name, "ok": bool(ok), **details})
        print(json.dumps(report["checks"][-1], default=str), flush=True)

    for label, qs in (("active", active), ("public", public)):
        total = qs.count()
        missing = {
            "country": qs.filter(Q(country__isnull=True) | Q(country="")).count(),
            "salary": qs.filter(salary_min__isnull=True, salary_max__isnull=True).count(),
            "skills": qs.filter(Q(skills=[]) | Q(skills__isnull=True)).count(),
            "experience": qs.filter(Q(experience_level__isnull=True) | Q(experience_level="")).count(),
            "education": qs.filter(Q(education_level__isnull=True) | Q(education_level="")).count(),
            "category": qs.filter(category__isnull=True).count(),
            "search_vector": qs.filter(search_vector__isnull=True).count(),
            "embedding": qs.filter(embedding__isnull=True).count(),
        }
        report[label] = {
            "total": total, "missing": missing,
            "missing_pct": {k: round(v * 100 / max(total, 1), 1) for k, v in missing.items()},
            "sources": list(qs.values("source").annotate(total=Count("id")).order_by("-total")),
            "countries": list(qs.values("country").annotate(total=Count("id")).order_by("-total")[:20]),
            "older_than_90_days": qs.filter(posted_at__lt=now - timedelta(days=90)).count(),
            "quality_issues": dict(Counter(issue for issues in qs.values_list("quality_issues", flat=True) for issue in (issues or []))),
        }
    invalid = public.filter(Q(salary_min__gt=F("salary_max")) | Q(salary_min__lt=0) | Q(salary_max__lt=0))
    check("public_salary_ranges_valid", not invalid.exists(), invalid=list(invalid.values("id", "title", "salary_min", "salary_max")))
    check("public_expiry", not public.filter(expires_at__lt=now).exists())
    check("public_quality_floor", not public.filter(quality_score__gt=0, quality_score__lt=55).exists())
    check("public_search_vectors", not public.filter(search_vector__isnull=True).exists())
    check("public_required_listing_text", not public.filter(Q(title="") | Q(description="")).exists())
    duplicate_groups = list(public.values("organization_id", "title", "location").annotate(n=Count("id")).filter(n__gt=1).order_by("-n")[:20])
    report["duplicate_candidates"] = duplicate_groups
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename='jobs_job'")
        report["job_indexes"] = [dict(zip(("name", "definition"), row)) for row in cursor.fetchall()]

    def request_page(path):
        start = time.monotonic()
        try:
            with urlopen(Request("https://remoteimpact.org" + path, headers={"User-Agent": "RemoteImpact-QA/1.0"}), timeout=90) as response:
                status = response.status
                html = response.read().decode()
            parser = Page()
            parser.feed(html)
            return status, parser, round(time.monotonic() - start, 3), html
        except Exception as exc:
            return 0, Page(), round(time.monotonic() - start, 3), str(exc)

    scenarios = [
        ("default", {}), ("page_two", {"page": "2"}),
        ("software_engineer", {"q": "software engineer"}),
        ("python", {"q": "python"}),
        ("python_form_defaults", {"q": "python", "salary_min": "0", "salary_max": "1000000"}),
        ("python_newest", {"q": "python", "sort": "date"}),
        ("python_relevance", {"q": "python", "sort": "relevance"}),
        ("salary_high", {"sort": "salary-high"}),
        ("salary_high_page_two", {"sort": "salary-high", "page": "2"}),
        ("salary_low", {"sort": "salary-low"}),
        ("has_salary", {"has_salary": "1"}),
        ("salary_overlap", {"salary_min": "75000", "salary_max": "150000"}),
        ("country", {"country": "Germany"}),
        ("category", {"category": "climate-environment"}),
        ("multiple_filters", [("category", "climate-environment"), ("category", "ai-safety"), ("type", "contract"), ("type", "part-time")]),
        ("skill", {"skill": "python"}),
        ("experience", {"experience": "senior"}),
        ("education", {"education": "bachelor"}),
        ("posted_week", {"posted": "7"}),
        ("direct_apply", {"direct_apply": "1"}),
        ("no_results", {"q": "zzzznonexistentrolezzzz"}),
    ]
    for name, params in scenarios:
        query = QueryDict(urlencode(params, doseq=True))
        qs = JobService.get_filtered_jobs(query)
        if query.get("sort") == "salary-high":
            qs = qs.order_by(Coalesce("salary_max", "salary_min").desc(nulls_last=True), "-posted_at", "-id")
        elif query.get("sort") == "salary-low":
            qs = qs.order_by(Coalesce("salary_min", "salary_max").asc(nulls_last=True), "-posted_at", "-id")
        page_number = int(query.get("page", "1"))
        expected = list(qs.values_list("slug", flat=True)[(page_number - 1) * 20:page_number * 20])
        status, page, elapsed, html = request_page("/jobs/?" + query.urlencode())
        check("http_" + name, status == 200, seconds=elapsed)
        check("results_" + name, page.slugs == expected, expected_count=qs.count(), displayed=len(page.slugs), expected=expected[:5], actual=page.slugs[:5])
        if name == "multiple_filters" and page.next_url:
            next_query = QueryDict(page.next_url.split("?", 1)[1])
            check("pagination_multiselect", set(next_query.getlist("category")) == set(query.getlist("category")) and set(next_query.getlist("type")) == set(query.getlist("type")), next_url=page.next_url)
    unrestricted = JobService.get_filtered_jobs({"q": "python"}).count()
    form_default = JobService.get_filtered_jobs({"q": "python", "salary_min": "0", "salary_max": "1000000"}).count()
    check("default_salary_does_not_hide_unknown", unrestricted == form_default, unrestricted=unrestricted, form_default=form_default)
    for short, full in (("USA", "United States"), ("UK", "United Kingdom"), ("Global", "Worldwide")):
        left = set(JobService.get_filtered_jobs({"country": short}).values_list("pk", flat=True))
        right = set(JobService.get_filtered_jobs({"country": full}).values_list("pk", flat=True))
        check("country_alias_" + short, left == right, short_count=len(left), full_count=len(right))

    for path in ("/", "/tools/", "/pricing/", "/resources/remote-impact-jobs/", "/insights/remote-impact-jobs/", "/feed/jobs/", "/accounts/login/", "/accounts/signup/"):
        status, page, elapsed, html = request_page(path)
        check("route_" + path, status == 200, seconds=elapsed)
    from jobs.urls import urlpatterns
    for route in urlpatterns:
        path = str(route.pattern)
        if path.startswith("tools/") and path.endswith("/") and not any(
            word in path for word in ("generate/", "analyze/", "null", "<")
        ) and path != "tools/":
            status, page, elapsed, html = request_page("/" + path)
            check("tool_page_" + path, status == 200, seconds=elapsed)
    for slug in list(public.order_by("-posted_at", "-id").values_list("slug", flat=True)[:3]):
        status, page, elapsed, html = request_page("/jobs/" + slug + "/")
        check("detail_" + slug, status == 200 and "JobPosting" in html, seconds=elapsed)

    for seeker in SeekerProfile.objects.filter(wizard_completed=True).exclude(skills=[]).select_related("user")[:2]:
        request = RequestFactory().get("/jobs/", {"sort": "best-match"})
        request.user = seeker.user
        view = JobListView()
        view.request = request
        start = time.monotonic()
        jobs = list(view.get_queryset()[:20])
        dates = [(j.posted_at, j.id) for j in jobs]
        check("personalized_chronological_" + str(seeker.pk), dates == sorted(dates, reverse=True), seconds=round(time.monotonic() - start, 3), count=len(jobs))

    briefs = [
        "Remote software engineering roles using Python in Europe",
        "Remote entry level software engineer jobs worldwide, exclude sales and internships",
        "Remote senior data analyst jobs in the United States with at least $100000 salary",
        "Remote impact investing roles using investment management and portfolio management",
        "Remote humanitarian programme manager roles in Africa",
    ]
    for intent in briefs:
        start = time.monotonic()
        prefs = JobScoutService.parse_intent(intent)
        diagnostics = {}
        matches = JobScoutService.get_matches_for_preferences(prefs, since=now - timedelta(days=30), limit=5, diagnostics=diagnostics)
        item = {"intent": intent, "preferences": prefs, "seconds": round(time.monotonic() - start, 3), "diagnostics": diagnostics, "jobs": [{"id": j.pk, "title": j.title, "location": j.location, "score": j.scout_score} for j in matches]}
        report["scouts"].append(item)
        print(json.dumps({"scout": item}), flush=True)
        check("scout_live_jobs_" + str(len(report["scouts"])), all(j.is_active and (not j.expires_at or j.expires_at >= now) and j.posted_at >= now - timedelta(days=30) for j in matches))
    report["recurring_alerts"] = {"active": JobAlert.objects.filter(is_active=True).count(), "active_pro_subscriptions": sum(s.is_pro_active() for s in AssistantSubscription.objects.all())}
    report["summary"] = {"passed": sum(c["ok"] for c in report["checks"]), "failed": sum(not c["ok"] for c in report["checks"])}
    print("AUDIT_REPORT=" + json.dumps(report, default=str), flush=True)


run()
