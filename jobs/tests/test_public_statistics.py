from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from jobs.models import PublicStatisticSnapshot
from jobs.services.trust_stats import trust_stats


class PublicStatisticsTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_cold_public_read_uses_durable_snapshot_not_job_scan(self):
        now = timezone.now()
        PublicStatisticSnapshot.objects.create(key='trust', payload={'live': 42}, computed_at=now)
        with self.assertNumQueries(1):
            self.assertEqual(trust_stats(), {'live': 42, 'computed_at': now})
        cache.clear()
        with self.assertNumQueries(1):
            self.assertEqual(trust_stats()['live'], 42)

    def test_missing_snapshot_does_not_compute_or_invent_zero_counts(self):
        with self.assertNumQueries(1):
            self.assertEqual(trust_stats(), {})

    def test_explicit_refresh_persists_after_cache_clear(self):
        call_command('refresh_public_statistics')
        saved=PublicStatisticSnapshot.objects.get(pk='trust')
        self.assertEqual(saved.payload['live'], 0)
        cache.clear()
        with self.assertNumQueries(1):
            self.assertEqual(trust_stats()['computed_at'], saved.computed_at)
