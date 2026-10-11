from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sites.models import Site
from django.test import TestCase
from django.test.client import RequestFactory
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from jobs.models import AssistantSubscription, Job, Organization
from jobs.models import SeekerProfile
from jobs.services.job_service import JobService
from jobs.views.jobs import JobListView
from unittest.mock import patch
from allauth.socialaccount.models import SocialApp


class JobListOrderingTest(TestCase):
    def setUp(self):
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider, provider_id=provider_id, name=provider,
                client_id="test-client", secret="test-secret",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(Site.objects.get_current())

    def test_full_salary_slider_range_keeps_unknown_and_uncapped_jobs(self):
        organization = Organization.objects.create(name="Salary Default Org")
        jobs = [Job.objects.create(
            title=f"Available role {i}", slug=f"salary-default-{i}", organization=organization,
            salary_min=minimum, salary_max=maximum,
        ) for i, (minimum, maximum) in enumerate(((None, None), (60000, 80000), (1500000, 2000000)))]
        results = JobService.get_filtered_jobs({"salary_min": "0", "salary_max": "1000000"})
        self.assertEqual(set(results.values_list("pk", flat=True)), {job.pk for job in jobs})
        restricted = JobService.get_filtered_jobs({"salary_min": "75000", "salary_max": "150000"})
        self.assertEqual(list(restricted.values_list("pk", flat=True)), [jobs[1].pk])

    def test_salary_sort_is_global_before_pagination_and_nulls_are_last(self):
        organization = Organization.objects.create(name="Salary Sort Org")
        now = timezone.now()
        jobs = [Job.objects.create(
            title=f"Salary role {i}", slug=f"global-salary-sort-{i}", organization=organization,
            salary_min=50000 + i * 1000, salary_max=60000 + i * 1000,
            posted_at=now - timedelta(days=i),
        ) for i in range(25)]
        unknown = Job.objects.create(title="Unknown salary", slug="unknown-global-salary", organization=organization)
        high = self.client.get(reverse("jobs:job_list"), {"sort": "salary-high"})
        self.assertEqual([j.pk for j in high.context["jobs"]], [j.pk for j in reversed(jobs)][:20])
        low = self.client.get(reverse("jobs:job_list"), {"sort": "salary-low", "page": 2})
        self.assertEqual([j.pk for j in low.context["jobs"]], [j.pk for j in jobs[20:]] + [unknown.pk])

    def test_salary_sort_uses_known_single_bound(self):
        organization = Organization.objects.create(name="Single Bound Salary Org")
        low = Job.objects.create(title="Low", slug="low-single-bound", organization=organization, salary_max=70000)
        high = Job.objects.create(title="High", slug="high-single-bound", organization=organization, salary_min=90000)
        self.assertEqual(list(JobService.get_filtered_jobs({"sort": "salary-high"}).values_list("pk", flat=True)), [high.pk, low.pk])
        self.assertEqual(list(JobService.get_filtered_jobs({"sort": "salary-low"}).values_list("pk", flat=True)), [low.pk, high.pk])

    @patch("jobs.views.jobs.JobListView._get_recommended_organizations", return_value=[])
    def test_pagination_preserves_multiselects_and_encodes_query(self, recommendations):
        request = RequestFactory().get("/jobs/?category=climate-environment&category=ai-safety&type=contract&type=part-time&q=research%26policy")
        request.user = AnonymousUser()
        response = JobListView.as_view()(request)
        response.render()
        params = QueryDict(response.context_data["pagination_query"])
        self.assertEqual(params.getlist("category"), ["climate-environment", "ai-safety"])
        self.assertEqual(params.getlist("type"), ["contract", "part-time"])
        self.assertEqual(params["q"], "research&policy")

    def test_country_aliases_and_word_boundaries(self):
        organization = Organization.objects.create(name="Country Alias Org")
        usa = Job.objects.create(title="US role", slug="us-country-alias", organization=organization, country="United States", location="Remote (US)")
        uk = Job.objects.create(title="UK role", slug="uk-country-alias", organization=organization, country="United Kingdom", location="Remote (UK)")
        india = Job.objects.create(title="India role", slug="india-country-alias", organization=organization, location="India")
        Job.objects.create(title="Indiana role", slug="indiana-not-india", organization=organization, location="Indiana, USA")
        self.assertIn(usa.pk, JobService.get_filtered_jobs({"country": "USA"}).values_list("pk", flat=True))
        self.assertEqual(list(JobService.get_filtered_jobs({"country": "UK"}).values_list("pk", flat=True)), [uk.pk])
        self.assertEqual(list(JobService.get_filtered_jobs({"country": "India"}).values_list("pk", flat=True)), [india.pk])

    def test_worldwide_country_filter_does_not_include_us_only_remote(self):
        organization = Organization.objects.create(name="Worldwide Filter Org")
        worldwide = Job.objects.create(title="Global role", slug="worldwide-filter-role", organization=organization, country="Worldwide", location="Remote")
        Job.objects.create(title="US role", slug="us-only-worldwide-filter", organization=organization, country="USA", location="Remote (US)")
        for name in ("Worldwide", "Global"):
            self.assertEqual(list(JobService.get_filtered_jobs({"country": name}).values_list("pk", flat=True)), [worldwide.pk])

    @patch("jobs.services.unified_matching_service.UnifiedMatchingService.get_matches", return_value=[])
    def test_empty_personalized_matches_fallback_is_newest_first(self, matches):
        from jobs.services.unified_matching_service import UnifiedMatchingService

        organization = Organization.objects.create(name="Empty Matches Ordering Org")
        old = Job.objects.create(title="Old featured", slug="empty-old-featured", organization=organization, is_featured=True, posted_at=timezone.now() - timedelta(days=30))
        new = Job.objects.create(title="New", slug="empty-new", organization=organization)
        result = UnifiedMatchingService.order_jobs_for_seeker(Job.objects.all(), None)
        self.assertEqual(list(result.values_list("pk", flat=True)), [new.pk, old.pk])

    def test_anonymous_job_list_visitor_sees_scout_entry_point(self):
        request = RequestFactory().get(reverse("jobs:job_list"))
        request.user = AnonymousUser()
        view = JobListView()
        view.request = request
        view.args = ()
        view.kwargs = {}
        view.object_list = Job.objects.none()

        context = view.get_context_data(object_list=view.object_list)

        self.assertTrue(context["show_job_scout_nudge"])
        self.assertEqual(
            context["job_scout_nudge_url"], "/?scout=1&source=job_list_organic"
        )
        self.assertEqual(context["job_scout_nudge_label"], "Describe your ideal job")

    def test_signed_in_new_user_sees_one_time_job_scout_nudge(self):
        user = get_user_model().objects.create_user(
            username="new-scout-user", email="new-scout@example.com", password="test-pass"
        )
        self.client.force_login(user)

        response = self.client.get(reverse("jobs:job_list"))

        self.assertContains(response, "Want a shorter, more relevant list?")
        self.assertContains(response, "source=job_list_nudge")

    def test_used_free_scout_sees_pro_nudge_instead_of_free_offer(self):
        user = get_user_model().objects.create_user(
            username="used-scout-user",
            email="used-scout@example.com",
            password="test-pass",
        )
        subscription = AssistantSubscription.get_or_create_for_user(user)
        subscription.free_job_scout_used_at = timezone.now()
        subscription.save(update_fields=["free_job_scout_used_at"])
        self.client.force_login(user)

        response = self.client.get(reverse("jobs:job_list"))

        self.assertContains(response, "Keep the right jobs coming")
        self.assertContains(response, "job_list_pro_nudge")
        self.assertNotContains(response, "Want a shorter, more relevant list?")

    @patch("jobs.views.jobs.UnifiedMatchingService.order_jobs_for_seeker")
    def test_completed_profile_with_used_free_scout_keeps_pro_nudge(self, order_jobs):
        user = get_user_model().objects.create_user(
            username="completed-used-scout-user",
            email="completed-used-scout@example.com",
            password="test-pass",
        )
        SeekerProfile.objects.create(
            user=user,
            wizard_completed=True,
            headline="Python engineer",
            skills=["python"],
        )
        subscription = AssistantSubscription.get_or_create_for_user(user)
        subscription.free_job_scout_used_at = timezone.now()
        subscription.save(update_fields=["free_job_scout_used_at"])
        self.client.force_login(user)
        order_jobs.return_value = Job.objects.none()

        response = self.client.get(reverse("jobs:job_list"))

        self.assertContains(response, "Keep the right jobs coming")
        self.assertContains(response, "Keep getting matches")
        self.assertContains(response, "job_list_pro_nudge")
        self.assertNotContains(response, "Email my matches")

    @patch("jobs.views.jobs.UnifiedMatchingService.order_jobs_for_seeker")
    def test_completed_profile_nudge_prefills_job_scout(self, order_jobs):
        user = get_user_model().objects.create_user(
            username="completed-nudge-user",
            email="completed-nudge@example.com",
            password="test-pass",
        )
        SeekerProfile.objects.create(
            user=user,
            wizard_completed=True,
            headline="Python engineer",
            skills=["python"],
        )
        organization = Organization.objects.create(name="Completed Profile Org")
        Job.objects.create(
            title="Python role",
            slug="completed-profile-python-role",
            organization=organization,
            description="A current role.",
        )
        order_jobs.return_value = Job.objects.all()
        self.client.force_login(user)

        response = self.client.get(reverse("jobs:job_list"))

        self.assertContains(response, "Email my matches")
        self.assertContains(response, "source=profile_complete")
        self.assertContains(response, "Python+engineer")

    def test_default_feed_is_newest_first_even_when_older_job_is_featured(self):
        organization = Organization.objects.create(name="Ordering Test Org")
        now = timezone.now()
        jobs = Job.objects.bulk_create(
            [
                Job(
                    title="Older Featured Role",
                    slug="older-featured-role",
                    organization=organization,
                    description="Description",
                    requirements="Requirements",
                    posted_at=now - timedelta(days=30),
                    is_featured=True,
                ),
                Job(
                    title="Newest Role",
                    slug="newest-role",
                    organization=organization,
                    description="Description",
                    requirements="Requirements",
                    posted_at=now - timedelta(hours=1),
                ),
            ]
        )

        result_ids = list(
            JobService.get_filtered_jobs({}).values_list("id", flat=True)
        )

        self.assertEqual(result_ids, [jobs[1].id, jobs[0].id])

    def test_public_feed_hides_known_low_quality_jobs(self):
        organization = Organization.objects.create(name="Quality Ordering Org")
        now = timezone.now()
        low_quality = Job.objects.create(
            title="Incomplete imported role",
            slug="incomplete-imported-role",
            organization=organization,
            description="Too little verified listing data.",
            quality_score=43,
            posted_at=now,
        )
        good_quality = Job.objects.create(
            title="Verified current role",
            slug="verified-current-role",
            organization=organization,
            description="A complete current job listing.",
            quality_score=80,
            posted_at=now - timedelta(hours=1),
        )

        result_ids = list(
            JobService.get_filtered_jobs({}).values_list("id", flat=True)
        )

        self.assertNotIn(low_quality.id, result_ids)
        self.assertIn(good_quality.id, result_ids)

    def test_search_defaults_to_strict_newest_order(self):
        queryset = JobService.get_filtered_jobs({"q": "python"})

        self.assertEqual(queryset.query.order_by, ("-posted_at", "-id"))

    def test_search_relevance_order_is_only_used_when_requested(self):
        queryset = JobService.get_filtered_jobs(
            {"q": "python", "sort": "relevance"}
        )

        self.assertEqual(
            queryset.query.order_by,
            (
                "-title_phrase_match",
                "-title_all_words_match",
                "-fts_rank",
                "-posted_at",
                "-id",
            ),
        )

    @patch("jobs.views.jobs.UnifiedMatchingService.order_jobs_for_seeker")
    def test_completed_profile_can_open_personalized_job_feed(self, order_jobs):
        user = get_user_model().objects.create_user(
            username="personalized-user",
            email="personalized@example.com",
            password="test-pass",
        )
        SeekerProfile.objects.create(user=user, wizard_completed=True)
        organization = Organization.objects.create(name="Default Personalized Org")
        job = Job.objects.create(
            title="Relevant role",
            slug="default-personalized-relevant-role",
            organization=organization,
            description="A current role.",
        )
        order_jobs.return_value = Job.objects.filter(pk=job.pk)
        self.client.force_login(user)

        response = self.client.get(reverse("jobs:job_list"), {"sort": "best-match"})

        self.assertEqual(response.status_code, 200)
        order_jobs.assert_called_once()
        self.assertContains(response, "Best for you")

    @patch("jobs.views.jobs.UnifiedMatchingService.order_jobs_for_seeker")
    def test_completed_profile_uses_personalized_feed_by_default(self, order_jobs):
        user = get_user_model().objects.create_user(
            username="default-personalized-user",
            email="default-personalized@example.com",
            password="test-pass",
        )
        SeekerProfile.objects.create(user=user, wizard_completed=True)
        organization = Organization.objects.create(name="Default Personalized Org")
        job = Job.objects.create(
            title="Relevant role",
            slug="default-personalized-relevant-role",
            organization=organization,
            description="A current role.",
        )
        order_jobs.return_value = Job.objects.filter(pk=job.pk)
        self.client.force_login(user)

        response = self.client.get(reverse("jobs:job_list"))

        self.assertEqual(response.status_code, 200)
        order_jobs.assert_called_once()
        self.assertContains(response, "Best for you")
        self.assertNotContains(response, 'value="date" selected')

    def test_multi_word_role_search_requires_all_words_in_title(self):
        organization = Organization.objects.create(name="Search Precision Org")
        now = timezone.now()
        Job.objects.create(
            title="Software Engineer",
            slug="software-engineer-search-result",
            organization=organization,
            description="Build reliable systems.",
            posted_at=now - timedelta(hours=2),
        )
        Job.objects.create(
            title="Commercial Director",
            slug="commercial-director-search-noise",
            organization=organization,
            description="Manage a software engineer hiring program.",
            posted_at=now - timedelta(hours=1),
        )

        titles = list(
            JobService.get_filtered_jobs({"q": "software engineer"})
            .values_list("title", flat=True)
        )

        self.assertEqual(titles, ["Software Engineer"])


class JobDetailAvailabilityTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="detail-viewer", password="test-pass-123"
        )
        self.client.force_login(self.user)
        self.organization = Organization.objects.create(
            name="Detail Test Org", slug="detail-test-org"
        )

    def test_inactive_job_keeps_useful_closed_page_without_job_schema(self):
        job = Job.objects.create(
            title="Closed Engineer",
            slug="closed-engineer",
            organization=self.organization,
            description="An old role retained for reference.",
            application_url="https://example.org/apply",
            posted_at=timezone.now() - timedelta(days=20),
            is_active=False,
        )

        response = self.client.get(reverse("jobs:job_detail", args=[job.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'content="noindex, follow"')
        self.assertContains(response, "This listing is no longer available on Remote Impact")
        self.assertNotContains(response, '"@type": "JobPosting"')
        self.assertNotContains(response, "Apply on company site")

    def test_active_job_remains_indexable_with_job_schema(self):
        job = Job.objects.create(
            title="Open Engineer",
            slug="open-engineer",
            organization=self.organization,
            description="A current role.",
            location="Remote (US)",
            posted_at=timezone.now() - timedelta(days=2),
            is_active=True,
        )

        response = self.client.get(reverse("jobs:job_detail", args=[job.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'content="index, follow"')
        self.assertContains(response, '"@type": "JobPosting"')
        self.assertNotContains(response, "This listing is no longer available on Remote Impact")

    def test_job_detail_routes_similar_job_interest_to_ai_scout(self):
        job = Job.objects.create(
            title="Climate Data Analyst",
            slug="climate-data-analyst-scout-cta",
            organization=self.organization,
            description="A current role.",
            posted_at=timezone.now() - timedelta(days=1),
            is_active=True,
        )

        response = self.client.get(reverse("jobs:job_detail", args=[job.slug]))

        self.assertContains(response, "Want more roles like this?")
        self.assertContains(response, "source=job_detail")
        self.assertContains(response, "job_scout_job_detail")
        self.assertNotContains(response, 'id="job-alert-form"')

    def test_closed_application_guide_redirects_to_closed_job_page(self):
        job = Job.objects.create(
            title="Closed Guide Role",
            slug="closed-guide-role",
            organization=self.organization,
            description="An old role retained for reference.",
            posted_at=timezone.now() - timedelta(days=20),
            is_active=False,
        )

        response = self.client.get(
            reverse("jobs:application_guide", args=[job.slug])
        )

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, reverse("jobs:job_detail", args=[job.slug]))
