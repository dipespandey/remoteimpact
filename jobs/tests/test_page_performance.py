from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from jobs.models import (Application, Category, Job, JobInterest, Organization,
                         SeekerProfile, SourcingSuggestion, TalentInvitation)
from jobs.services.page_cache import cache_public_page
from jobs.services.candidate_suggestions import suggested_candidates_for_jobs
from jobs.services.job_service import JobService
from jobs.views.dashboard import DashboardView


@override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
class PublicPageCacheTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.calls = 0

        @cache_public_page(60)
        def view(request):
            self.calls += 1
            return HttpResponse(f"{request.user.username}|{request.COOKIES.get('sessionid', '')}|{request.headers.get('HX-Request', '')}|{request.GET.get('q', '')}")
        self.view = view

    def request(self, cookie="", authenticated=False, username="anonymous", **headers):
        request = self.factory.get("/jobs/", HTTP_COOKIE=cookie, **headers)
        request.user = SimpleNamespace(is_authenticated=authenticated, username=username)
        return request

    def test_repeated_anonymous_request_is_cached_and_cookies_are_isolated(self):
        self.view(self.request("sessionid=first"))
        self.view(self.request("sessionid=first"))
        response = self.view(self.request("sessionid=second"))
        self.assertEqual(self.calls, 2)
        self.assertIn(b"second", response.content)
        self.assertIn("Cookie", response["Vary"])

    def test_authenticated_requests_bypass_an_existing_anonymous_cache_entry(self):
        self.view(self.request("sessionid=same"))
        for name in ("Alice", "Bob"):
            response = self.view(self.request("sessionid=same", authenticated=True, username=name))
            self.assertIn(name.encode(), response.content)
        self.assertEqual(self.calls, 3)

    def test_htmx_and_full_page_responses_have_different_cache_entries(self):
        self.view(self.request())
        response = self.view(self.request(HTTP_HX_REQUEST="true"))
        self.assertEqual(self.calls, 2)
        self.assertIn(b"true", response.content)

    def test_query_strings_do_not_share_cached_results(self):
        for query in ("python", "climate"):
            request = self.request()
            request.GET = request.GET.copy()
            request.GET["q"] = query
            request.META["QUERY_STRING"] = f"q={query}"
            self.assertIn(query.encode(), self.view(request).content)
        self.assertEqual(self.calls, 2)


class DashboardQueryTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.users = User.objects.bulk_create([User(username=f"query-{i}", email=f"query-{i}@example.org") for i in range(10)])
        self.org = Organization.objects.create(name="Query Org", slug="query-org")
        self.cat = Category.objects.create(name="Climate", slug="climate-environment")
        now = timezone.now()
        self.jobs = Job.objects.bulk_create([Job(title=f"Analyst {i}", slug=f"query-job-{i}",
            organization=self.org, category=self.cat, poster=self.users[0], source="manual", is_paid=True,
            description="Analyze climate programs with Python and SQL. " * 35,
            requirements="Python and SQL", skills=["python", "sql"], expires_at=now + timedelta(days=30)) for i in range(30)])

    def rows(self):
        return list(Job.objects.select_related("organization", "category").order_by("pk"))

    def test_hiring_counts_use_constant_queries_and_preserve_consent_and_screening(self):
        Application.objects.bulk_create([Application(job=self.jobs[0], applicant=self.users[i],
            screening={"met": 1} if i < 3 else {}) for i in range(1, 4)])
        now = timezone.now()
        JobInterest.objects.bulk_create([
            JobInterest(job=self.jobs[0], user=self.users[1], share_with_employer=True, consented_at=now),
            JobInterest(job=self.jobs[0], user=self.users[2], share_with_employer=False, consented_at=now),
            JobInterest(job=self.jobs[0], user=self.users[3], share_with_employer=True, consented_at=now, status="archived"),
            JobInterest(job=self.jobs[0], user=self.users[4], share_with_employer=True, consented_at=now-timedelta(days=91)),
        ])
        rows = self.rows()
        with CaptureQueriesContext(connection) as queries:
            result = DashboardView._hiring_overview(rows)
        self.assertLessEqual(len(queries), 4)
        self.assertEqual(result["total_candidates"], 3)
        self.assertEqual(result["interested_total"], 1)
        self.assertEqual((rows[0].applicant_count, rows[0].screened_count), (3, 2))

    def test_sourcing_hydrates_only_four_uninvited_people_per_role(self):
        profiles = SeekerProfile.objects.bulk_create([SeekerProfile(user=u) for u in self.users[1:8]])
        SourcingSuggestion.objects.bulk_create([SourcingSuggestion(job=self.jobs[0], seeker=p) for p in profiles])
        TalentInvitation.objects.bulk_create([TalentInvitation(job=self.jobs[0], seeker=profiles[-1], organization=self.org)])
        result = DashboardView._hiring_overview(self.rows())
        sourced = result["live_jobs"][0].sourced
        self.assertEqual(len(sourced), 4)
        self.assertNotIn(profiles[-1].pk, [s.seeker_id for s in sourced])

    def test_warm_candidate_batch_rechecks_visibility_invites_and_current_embeddings(self):
        vector = [1.0] + [0.0] * 383
        Job.objects.filter(pk__in=[j.pk for j in self.jobs[:2]]).update(embedding=vector)
        profiles = SeekerProfile.objects.bulk_create([SeekerProfile(user=u, wizard_completed=True,
            is_actively_looking=True, visibility="public", skills=["python", "sql"], embedding=vector) for u in self.users[1:4]])
        rows = self.rows()[:2]
        cold = suggested_candidates_for_jobs(rows)
        self.assertEqual(len(cold[rows[0].pk]), 3)
        SeekerProfile.objects.filter(pk=profiles[0].pk).update(visibility="hidden")
        SeekerProfile.objects.filter(pk=profiles[1].pk).update(embedding=[0.0, 1.0] + [0.0] * 382)
        TalentInvitation.objects.bulk_create([TalentInvitation(job=rows[0], seeker=profiles[2], organization=self.org)])
        with CaptureQueriesContext(connection) as queries:
            warm = suggested_candidates_for_jobs(rows)
        self.assertLessEqual(len(queries), 3)
        self.assertEqual(warm[rows[0].pk], [])
        self.assertEqual([s.profile.pk for s in warm[rows[1].pk]], [profiles[2].pk])

    def test_api_search_collapses_variants_only_once(self):
        from jobs.api.auth import Caller
        from jobs.api.rest import search
        with mock.patch.object(JobService, "_collapse_location_variants", wraps=JobService._collapse_location_variants) as collapse:
            rows, total, _, _ = search({}, Caller(tier="anonymous"))
        collapse.assert_called_once()
        self.assertEqual(total, 30)
        self.assertEqual(len(rows), 10)

    def test_api_filters_before_selecting_the_representative_variant(self):
        Job.objects.filter(pk=self.jobs[0].pk).update(title="Regional Analyst", location="Remote, Worldwide")
        Job.objects.filter(pk=self.jobs[1].pk).update(title="Regional Analyst", location="Remote, United States", posted_at=timezone.now()+timedelta(seconds=1))
        from jobs.api.auth import Caller
        from jobs.api.rest import search
        rows, total, _, _ = search({"remote_scope": "worldwide"}, Caller(tier="anonymous"))
        self.assertEqual(total, 1)
        self.assertEqual([j.pk for j in rows], [self.jobs[0].pk])

    def test_api_compression_preserves_json_and_cache_directives(self):
        import gzip
        import json
        response = self.client.get("/api/v1/jobs/", HTTP_ACCEPT_ENCODING="gzip")
        self.assertEqual(response["Content-Encoding"], "gzip")
        body = gzip.decompress(response.content)
        self.assertEqual(len(json.loads(body)["jobs"]), 10)
        self.assertLess(len(response.content), len(body) // 2)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("Accept-Encoding", response["Vary"])

    def test_trust_stats_aggregate_removals_without_double_counting(self):
        from jobs.services.trust_stats import trust_stats
        now = timezone.now()
        old = now - timedelta(days=8)
        raws = [
            {"source_verification": {"status": "closed", "checked_at": now.isoformat()}, "removed_from_source_at": now.isoformat()},
            {"source_verification": {"status": "not_remote", "checked_at": now.isoformat()}},
            {"source_verification": {"status": "dead", "checked_at": old.isoformat()}, "removed_from_source_at": now.isoformat()},
            {"removed_from_source_at": now.isoformat()},
            {"source_verification": {"status": "dead", "checked_at": now.isoformat()}},
            {"source_verification": {"status": "closed", "checked_at": old.isoformat()}},
        ]
        for job, raw in zip(self.jobs, raws):
            Job.objects.filter(pk=job.pk).update(is_active=False, raw_data=raw)
        stats = trust_stats(refresh=True)
        self.assertEqual((stats["removed_closed_7d"], stats["removed_not_remote_7d"], stats["removed_left_feed_7d"], stats["removed_7d"]), (2, 1, 2, 5))

    def test_empty_match_results_are_cached_briefly(self):
        from jobs.services.unified_matching_service import UnifiedMatchingService
        seeker = SeekerProfile.objects.create(user=self.users[1])
        with mock.patch.object(UnifiedMatchingService, "_retrieve_candidates", return_value=[]) as retrieve:
            self.assertEqual(UnifiedMatchingService.get_matches(seeker), [])
            self.assertEqual(UnifiedMatchingService.get_matches(seeker), [])
        retrieve.assert_called_once()

    def test_bulk_lexical_scoring_accepts_nonempty_terms_on_both_backends(self):
        from jobs.services.unified_matching_service import UnifiedMatchingService
        result = UnifiedMatchingService._bulk_lexical_scores([self.jobs[0].pk], "python")
        self.assertIsInstance(result, dict)
        if connection.vendor != "postgresql":
            self.assertEqual(result, {})
        else:
            self.assertEqual(result, {self.jobs[0].pk: 50.0})

    def test_warm_personalized_order_uses_ids_and_rechecks_closed_jobs(self):
        from jobs.services.unified_matching_service import UnifiedMatchingService
        seeker = SeekerProfile.objects.create(user=self.users[1])
        cache.set(UnifiedMatchingService._matches_cache_key(seeker, 100), [
            {"job_id": self.jobs[1].pk}, {"job_id": self.jobs[0].pk}], 60)
        Job.objects.filter(pk=self.jobs[1].pk).update(is_active=False)
        with mock.patch.object(UnifiedMatchingService, "get_matches", side_effect=AssertionError("Do not hydrate full matches")), \
                CaptureQueriesContext(connection) as queries:
            rows = UnifiedMatchingService.order_jobs_for_seeker(JobService.get_available_jobs(), seeker)
            self.assertEqual(list(rows.values_list("pk", flat=True)), [self.jobs[0].pk])
        self.assertEqual(len(queries), 2)

    def test_rate_counter_is_one_query_and_expired_windows_reset(self):
        from jobs.api.auth import _bump
        from jobs.models import ApiRateBucket
        with self.assertNumQueries(1):
            self.assertEqual(_bump("expiry-fixture", 120), 1)
        self.assertEqual(_bump("expiry-fixture", 120), 2)
        ApiRateBucket.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(_bump("expiry-fixture", 120), 1)

    @override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
    def test_real_job_page_cache_is_bypassed_after_sign_in(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider, client_id="x", secret="y")
            app.sites.add(Site.objects.get_current())
        url = "/jobs/?q=Analyst+29"
        self.client.get(url)  # establishes this browser's CSRF cookie
        self.assertContains(self.client.get(url), "Analyst 29")
        Job.objects.filter(pk=self.jobs[-1].pk).update(is_active=False)
        self.assertContains(self.client.get(url), "Analyst 29")
        self.client.force_login(self.users[0])
        response = self.client.get(url)
        self.assertNotIn(self.jobs[-1].pk, [j.pk for j in response.context["jobs"]])
        self.assertIn("private", response["Cache-Control"])
