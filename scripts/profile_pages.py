"""Repeatable page profiling against an isolated synthetic PostgreSQL database.

Requires DATABASE_URL naming remoteimpact_perf on localhost. Never seeds an
application database. Run --seed once, then save before/after JSON with --output.
"""
import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "jobboard.settings")
import django

django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from jobs.models import Application, Category, Job, Organization, SeekerProfile, Story, UserProfile


def seed():
    if Job.objects.exists():
        raise RuntimeError("Fixture already exists; refusing to overwrite it")
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site
    site = Site.objects.get_current()
    for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
        app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider,
            client_id="fixture", secret="fixture", settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {})
        app.sites.add(site)
    User = get_user_model()
    users = User.objects.bulk_create([User(username=f"perf-{i}", email=f"perf-{i}@example.org", password="!",
                                          first_name=f"Fixture {i}") for i in range(241)])
    UserProfile.objects.bulk_create([UserProfile(user=u, account_type="employer" if i == 0 else "seeker",
                                                headline="Climate data analyst") for i, u in enumerate(users)])
    orgs = Organization.objects.bulk_create([Organization(name=f"Fixture Org {i}", slug=f"perf-org-{i}",
        description="Impact organization", website=f"https://org-{i}.example.org", verification_status="verified") for i in range(120)])
    orgs[0].members.add(users[0])
    cats = Category.objects.bulk_create([Category(name=name, slug=slug) for name, slug in
        [("Climate & Environment", "climate-environment"), ("AI Safety", "ai-safety"), ("Global Health", "global-health")]])
    now = timezone.now()
    vector = [1.0] + [0.0] * 383
    profiles = SeekerProfile.objects.bulk_create([SeekerProfile(user=u, headline="Climate data analyst",
        bio="I analyze climate programs with Python and SQL.", wizard_completed=True, is_actively_looking=True,
        visibility="public", skills=["python", "sql"], experience_level="mid", remote_preference="remote",
        embedding=vector, country_eligibility="Worldwide") for u in users[1:]])
    through = SeekerProfile.impact_areas.through
    through.objects.bulk_create([through(seekerprofile=p, category=cats[0]) for p in profiles])
    jobs = Job.objects.bulk_create([Job(title=f"Climate Data Analyst {i}", slug=f"perf-job-{i}",
        organization=orgs[0] if i < 25 else orgs[i % len(orgs)], category=cats[i % len(cats)],
        description="<p>Analyze the climate data with Python and SQL for our impact programs.</p>" * 100,
        requirements="Python and SQL required. Three years of experience.",
        source="manual" if i < 25 else "feed", is_paid=i < 25, poster=users[0] if i < 25 else None,
        is_active=True, last_seen_at=now, posted_at=now - timedelta(minutes=i), expires_at=now + timedelta(days=30),
        location="Remote, Worldwide", country="Worldwide", job_type="full-time", experience_level="mid",
        salary_min=80000, salary_max=100000, salary_currency="USD", skills=["python", "sql"], embedding=vector,
        raw_data={"source_verification": {"method": "ats_api", "status": "open", "checked_at": now.isoformat()},
                  "source_description": "Source text " * 1500}) for i in range(5000)], batch_size=100)
    Application.objects.bulk_create([Application(job=j, applicant=u, screening={"met": 2}) for j in jobs[:25] for u in users[1:11]])
    Story.objects.bulk_create([Story(organization=orgs[i % 120], author_name=f"Fixture {i}", author_title="Analyst",
        content="Our team improved climate programs.", content_raw="Fixture text", status="published", published_at=now) for i in range(20)])
    print("Seeded 5,000 jobs, 120 organizations, 240 seekers and 250 applications")


def measure(output):
    from jobs.services.job_boards_service import CACHE_KEY, get_fallback_job_boards
    clients = {"anonymous": Client(), "employer": Client(), "seeker": Client()}
    users = get_user_model()
    clients["employer"].force_login(users.objects.get(username="perf-0"))
    clients["seeker"].force_login(users.objects.get(username="perf-1"))
    routes = [("anonymous", p) for p in ["/", "/jobs/", "/jobs/?q=analyst", "/jobs/?country=Worldwide",
        "/jobs/category/climate-environment/", "/jobs/perf-job-26/", "/organizations/", "/org/perf-org-1/",
        "/remote-jobs/worldwide/", "/careers/", "/browse/", "/salaries/", "/employers/", "/developers/",
        "/insights/remote-impact-jobs/", "/how-we-check/", "/resources/", "/tools/", "/pricing/", "/stories/",
        "/api/v1/jobs/"]]
    routes += [("employer", "/account/"), ("employer", "/talent/"), ("seeker", "/account/"),
               ("seeker", "/jobs/"), ("seeker", "/my-matches/")]
    report = {"fixture": {"jobs": Job.objects.count(), "organizations": Organization.objects.count(),
                           "seekers": SeekerProfile.objects.count()}, "routes": []}
    for persona, path in routes:
        cache.clear()
        cache.set(CACHE_KEY, get_fallback_job_boards(), 3600)
        runs = []
        for iteration in range(4):
            with CaptureQueriesContext(connection) as capture:
                start = time.perf_counter()
                response = clients[persona].get(path)
                elapsed = (time.perf_counter() - start) * 1000
            queries = list(capture.captured_queries)
            runs.append({"ms": round(elapsed, 2), "queries": len(queries), "status": response.status_code,
                         "sql_ms": round(sum(float(q["time"]) for q in queries) * 1000, 2)})
            if iteration == 0:
                slowest = sorted(queries, key=lambda q: float(q["time"]), reverse=True)[:3]
                repeated = Counter(q["sql"].split('WHERE')[0] for q in queries)
        row = {"persona": persona, "path": path, "cold": runs[0],
               "warm_ms": round(statistics.median(r["ms"] for r in runs[1:]), 2),
               "warm_queries": statistics.median(r["queries"] for r in runs[1:]),
               "slowest_sql": slowest, "repeated_query_shapes": repeated.most_common(4)}
        report["routes"].append(row)
        print(f"{persona:9} {path:40} {row['cold']['ms']:8.1f}ms / {row['cold']['queries']:3} queries; warm {row['warm_ms']:8.1f}ms / {row['warm_queries']:3}")
    Path(output).write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    db = settings.DATABASES["default"]
    if db.get("NAME") != "remoteimpact_perf" or db.get("HOST") not in ("127.0.0.1", "localhost"):
        raise RuntimeError("Use the isolated remoteimpact_perf database on localhost")
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"], DEBUG=True,
        INDEXNOW_ENABLED=False, EMAIL_PROVIDER="none", DEEPSEEK_API_KEY="",
        STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                  "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}},
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}):
        if args.seed:
            seed()
        measure(args.output)
