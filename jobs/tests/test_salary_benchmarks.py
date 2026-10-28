from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from jobs.models import Category, Job, Organization
from jobs.services.salary_benchmarks import benchmark_context


class SalaryBenchmarkEvidenceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Salary Evidence', slug='salary-evidence')
        self.category = Category.objects.create(name='Climate', slug='salary-climate')

    def job(self, slug, **changes):
        values = dict(title=slug, slug=slug, organization=self.org, category=self.category,
                      description='Annual employment.', source='manual', is_active=True,
                      posted_at=timezone.now(), salary_min=60000, salary_max=80000,
                      salary_currency='USD')
        values.update(changes)
        return Job.objects.create(**values)

    def test_only_current_supported_annual_usd_ranges_contribute_to_every_summary(self):
        supported = self.job('supported')
        self.job('eur', salary_currency='EUR', salary_min=100000, salary_max=200000)
        self.job('hourly', raw_data={'salaryPeriod': 'HOUR', 'salaryCurrency': 'USD'})
        self.job('fixed-term', description='$100,000–$167,000 USD for the four-month fellowship.')
        self.job('unsupported-import', source='ashby', raw_data={})
        self.job('expired', expires_at=timezone.now() - timedelta(days=1))
        self.job('inactive', is_active=False)
        self.job('reversed', salary_min=90000, salary_max=70000)
        self.job('one-sided', salary_max=None)
        with self.assertNumQueries(1):
            context = benchmark_context()
        self.assertEqual(context['total_with_salary'], 1)
        self.assertEqual(context['overall']['avg_min'], 60000)
        self.assertEqual(context['overall']['avg_max'], 80000)
        self.assertEqual(context['top_paying'], [supported])
        self.assertEqual(context['by_type'][0]['job_count'], 1)
        self.assertEqual(context['by_domain'], [])

    def test_no_supported_pay_has_an_explicit_empty_result(self):
        self.job('unsupported', source='ashby')
        context = benchmark_context()
        self.assertEqual(context['total_with_salary'], 0)
        self.assertIsNone(context['overall']['avg_min'])
        self.assertEqual(context['top_paying'], [])
