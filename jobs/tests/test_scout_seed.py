from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from jobs.models import Job, Organization, ScoutPreviewTask
from jobs.services.job_scout_service import JobScoutService
from jobs.services.scout_tasks import compute_task, semantic_key
from jobs.tests.test_scout_results import setup_scout_fixture

INTENT = "Remote Grants Manager roles similar to this one"


def make_job(org, title, slug, description="Manage grant applications, reporting and funder relationships."):
    with patch("jobs.services.embedding_service.embed_job", return_value=None):
        return Job.objects.create(title=title, slug=slug, organization=org, description=description,
                                  location="Remote", application_url=f"https://example.org/{slug}")


class SeedFixture:
    def make_jobs(self):
        self.org = Organization.objects.create(name="Seed Foundation", slug="seed-foundation")
        other = Organization.objects.create(name="Other Charity", slug="other-charity")
        self.seed = make_job(self.org, "Grants Manager", "grants-manager-seed")
        self.repost = make_job(self.org, "grants manager", "grants-manager-repost")
        self.peer = make_job(other, "Grants Manager", "grants-manager-peer")


@override_settings(DEEPSEEK_API_KEY="test", DEEPSEEK_MODEL="test", SCOUT_MULTISTEP_ENABLED=True)
class SimilarRolesSearchTests(SeedFixture, TestCase):
    def setUp(self):
        self.make_jobs()
        self.prompts = {}

    def fake_generate(self, prompt, **kwargs):
        self.prompts.setdefault(kwargs.get("stage"), []).append(prompt)
        return '{"decisions":[]}'

    @override_settings(SCOUT_RERANK_ENABLED=False)
    def test_similar_search_never_returns_the_job_it_started_from(self):
        with patch("jobs.services.scout_workflow.generate", side_effect=self.fake_generate):
            _, matches = JobScoutService.search_intent(INTENT, seed_job_id=self.seed.pk, limit=3)
        ids = {job.pk for job in matches}
        self.assertNotIn(self.seed.pk, ids)
        self.assertNotIn(self.repost.pk, ids)
        self.assertIn(self.peer.pk, ids)
        self.assertNotIn("parse", self.prompts)
        self.assertNotIn("plan", self.prompts)

    @override_settings(SCOUT_RERANK_ENABLED=True, SCOUT_RERANK_BACKEND="deepseek")
    def test_review_judges_candidates_against_the_seed_role(self):
        with patch("jobs.services.scout_workflow.generate", side_effect=self.fake_generate):
            JobScoutService.search_intent(INTENT, seed_job_id=self.seed.pk, limit=3)
        review = self.prompts["review"][0]
        self.assertIn("Organization: Seed Foundation", review)
        self.assertIn("funder relationships", review)

    @override_settings(SCOUT_RERANK_ENABLED=False)
    def test_a_closed_seed_falls_back_to_the_typed_search(self):
        self.seed.is_active = False
        self.seed.save(update_fields=["is_active"])
        with patch.object(JobScoutService, "parse_intent", wraps=JobScoutService.parse_intent) as parse, \
                patch("jobs.services.scout_workflow.generate", side_effect=self.fake_generate):
            JobScoutService.search_intent(INTENT, seed_job_id=self.seed.pk, limit=3)
        parse.assert_called_once()


@override_settings(SECURE_SSL_REDIRECT=False, SCOUT_PREVIEW_ASYNC_ENABLED=True)
class SimilarRolesPreviewTests(SeedFixture, TestCase):
    def setUp(self):
        setup_scout_fixture(self)
        self.make_jobs()

    def test_job_page_link_carries_the_seed_to_the_worker(self):
        url = reverse("jobs:job_scout_preview")
        with patch.object(JobScoutService, "search_intent"):
            self.client.get(url, {"intent": INTENT, "source": "job_detail", "similar_to": self.seed.pk})
            self.client.get(url, {"intent": INTENT, "source": "job_detail", "similar_to": self.seed.pk})
            self.client.get(url, {"intent": INTENT, "source": "job_detail"})
        seeded = ScoutPreviewTask.objects.get(seed_job_id=self.seed.pk)
        self.assertEqual(seeded.semantic_key, semantic_key(INTENT, self.seed.pk))
        self.assertEqual(ScoutPreviewTask.objects.count(), 2)
        with patch.object(JobScoutService, "search_intent", return_value=({}, [])) as search:
            compute_task(seeded.pk)
        self.assertEqual(search.call_args.kwargs["seed_job_id"], self.seed.pk)

    def test_anonymous_job_page_links_similar_roles_to_the_job(self):
        response = self.client.get(self.seed.get_absolute_url())
        self.assertContains(response, f"similar_to={self.seed.pk}")

    def test_ill_formed_seed_is_ignored(self):
        with patch.object(JobScoutService, "search_intent"):
            self.client.get(reverse("jobs:job_scout_preview"), {"intent": INTENT, "similar_to": "1;drop"})
        self.assertIsNone(ScoutPreviewTask.objects.get().seed_job_id)


class SimilarRolesFallbackTests(SeedFixture, TestCase):
    def setUp(self):
        self.make_jobs()

    def test_empty_result_lists_never_offer_the_job_it_started_from(self):
        from datetime import timedelta
        from django.utils import timezone
        from jobs.services.scout_alternatives import explore_openings, older_openings
        Job.objects.filter(pk__in=[self.seed.pk, self.repost.pk, self.peer.pk]).update(posted_at=timezone.now() - timedelta(days=45))
        preferences = {**JobScoutService._fallback_parse(INTENT), "_seed_job_id": self.seed.pk}
        with patch("jobs.services.scout_alternatives.duty_evidence", return_value="Manage grant applications"):
            older = older_openings({**preferences, "titles": ["grants manager"]}, limit=5)
        for listed in (explore_openings(preferences, limit=5), older):
            ids = {job.pk for job in listed}
            self.assertIn(self.peer.pk, ids)
            self.assertFalse(ids & {self.seed.pk, self.repost.pk})


class SimilarRolesLinkTests(TestCase):
    def test_contact_redirect_keeps_the_seed(self):
        from jobs.models import ConsumerFunnelEvent
        from jobs.views.job_alerts import _scout_preview_path
        ConsumerFunnelEvent.objects.create(event=ConsumerFunnelEvent.Event.SCOUT_PREVIEWED,
                                           metadata={"scout_flow_id": "f1", "intent_text": INTENT, "source": "job_detail", "seed_job_id": 42})
        self.assertIn("similar_to=42", _scout_preview_path({"id": "f1"}))

    def test_crafted_seed_values_are_ignored(self):
        from jobs.views.job_alerts import seed_job_id
        for value in ("²", "9" * 5000, "-1", "0", "1;drop", "", None, "4294967296"):
            self.assertIsNone(seed_job_id(value))
        self.assertEqual(seed_job_id("42"), 42)


class SemanticRankingShapeTests(TestCase):
    def test_vector_order_cannot_be_served_by_the_capped_hnsw_index(self):
        from django.db.models.expressions import CombinedExpression
        from jobs.services.scout_retrieval import semantic_ranking
        ranked = semantic_ranking(Job.objects.all(), [0.0, 1.0])
        self.assertEqual(ranked.query.order_by, ("scout_distance", "-id"))
        distance = ranked.query.annotations["scout_distance"].expression
        self.assertIsInstance(distance, CombinedExpression)
        self.assertEqual(distance.connector, "+")
