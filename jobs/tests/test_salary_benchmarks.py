from datetime import timedelta
import json
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from django.template.loader import render_to_string
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

    def test_domain_counts_and_public_markup_describe_the_actual_sample(self):
        for number in range(10):
            self.job(f'annual-{number}')
        self.job('foreign', salary_currency='EUR')
        context = benchmark_context()
        self.assertEqual(context['by_domain'][0]['job_count'], 10)
        self.assertEqual(context['by_domain'][0]['avg_max'], 80000)
        provider = Mock(get_login_url=Mock(return_value='/accounts/google/login/'))
        with patch('allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_provider', return_value=provider):
            html = render_to_string('jobs/tools/salary_benchmarks.html', context)
        soup = BeautifulSoup(html, 'html.parser')
        schemas = [json.loads(script.string) for script in soup.find_all('script', type='application/ld+json')]
        dataset = next(schema for schema in schemas if schema.get('@type') == 'Dataset')
        self.assertEqual(dataset['variableMeasured'][0]['unitText'], 'USD per year')
        self.assertFalse(any(schema.get('@type') == 'FAQPage' for schema in schemas))
        self.assertIn('Other currencies, hourly pay, fixed-term totals and unsupported estimates are excluded', soup.get_text(' ', strip=True))
