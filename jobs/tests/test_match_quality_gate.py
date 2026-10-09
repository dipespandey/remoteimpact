from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from jobs.models import Job, Organization
from jobs.services.unified_matching_service import UnifiedMatchingService


class MatchQualityGateTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Match Quality")

    def job(self, **kwargs):
        return Job.objects.create(title="Engineer", slug=f"match-quality-{Job.objects.count()}",
                                  organization=self.org, description="Python services " * 30, **kwargs)

    def test_cached_match_rechecks_expiry_and_quality(self):
        expired = self.job(expires_at=timezone.now() - timedelta(days=1), quality_score=80)
        thin = self.job(quality_score=54)
        self.assertEqual(UnifiedMatchingService._hydrate_cached_matches([
            {"job_id": expired.pk}, {"job_id": thin.pk},
        ]), [])

    @patch("jobs.services.unified_matching_service.embed_seeker", return_value=None)
    def test_nonvector_fallback_uses_public_quality_gate(self, _embed):
        expired = self.job(expires_at=timezone.now() - timedelta(days=1), quality_score=80)
        good = self.job(quality_score=80)
        result = UnifiedMatchingService._retrieve_candidates(SimpleNamespace(embedding=None))
        self.assertEqual([job.pk for job, score in result], [good.pk])
