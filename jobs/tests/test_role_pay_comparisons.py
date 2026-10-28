from unittest.mock import patch
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from jobs.models import Job, Category, Organization
from jobs.services.role_insights import _annual, category_stats, role_insights


class RolePayComparisonsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Pay Evidence', slug='pay-evidence')
        self.category = Category.objects.create(name='Climate', slug='pay-climate')

    def job(self, slug, **changes):
        values = dict(title=slug, slug=slug, organization=self.org, category=self.category,
            description='Annual role.', source='manual', salary_min=100000, salary_max=120000,
            salary_currency='USD', is_active=True, posted_at=timezone.now())
        values.update(changes)
        return Job.objects.create(**values)

    def test_supported_annual_midpoint_remains(self):
        self.assertEqual(_annual(self.job('annual')), 110000)

    def test_hourly_and_fixed_term_are_not_annual_comparisons(self):
        jobs = [self.job('hourly', source='ashby', raw_data={'salaryPeriod':'HOUR', 'salaryCurrency':'USD'}),
            self.job('term', description='$100,000–$167,000 USD for the four-month fellowship (annualized $300,000–$500,000).', salary_min=300000, salary_max=500000)]
        for job in jobs:
            with self.subTest(slug=job.slug):
                self.assertIsNone(_annual(job))
                with patch('jobs.services.role_insights.category_stats', return_value={'open':10, 'new_7d':2, 'mids':[100000]*8}):
                    self.assertNotIn('pay_mid', role_insights(job))

    def test_peer_population_uses_same_publication_gate_but_keeps_open_count(self):
        self.job('annual')
        self.job('term', description='$100,000–$167,000 USD for the four-month fellowship.')
        self.job('hourly', raw_data={'salaryPeriod':'HOUR','salaryCurrency':'USD'})
        with self.assertNumQueries(1):
            stats = category_stats(self.category.pk, 'full-time')
        self.assertEqual(stats['open'], 3)
        self.assertEqual(stats['mids'], [110000])
