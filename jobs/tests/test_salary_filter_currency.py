from django.test import TestCase
from django.http import QueryDict
from jobs.models import Job, Organization
from jobs.services.job_service import JobService

class SalaryFilterCurrencyTests(TestCase):
    def setUp(self):
        org=Organization.objects.create(name='Synthetic currencies',slug='synthetic-currencies')
        self.usd=Job.objects.create(title='Analyst USD',slug='analyst-usd',organization=org,salary_min=100000,salary_max=120000,salary_currency='USD')
        self.nok=Job.objects.create(title='Analyst NOK',slug='analyst-nok',organization=org,salary_min=1000000,salary_max=1200000,salary_currency='NOK')
        self.unknown=Job.objects.create(title='Unknown salary',slug='analyst-unknown',organization=org)
    def test_usd_lower_bound_does_not_compare_other_currency(self):
        self.assertEqual(set(JobService.get_filtered_jobs(QueryDict('salary_min=90000')).values_list('pk',flat=True)),{self.usd.pk})
    def test_usd_upper_bound_does_not_compare_other_currency(self):
        self.assertEqual(set(JobService.get_filtered_jobs(QueryDict('salary_max=150000')).values_list('pk',flat=True)),{self.usd.pk})
    def test_unbounded_defaults_preserve_unknown_and_other_currencies(self):
        self.assertEqual(set(JobService.get_filtered_jobs(QueryDict('salary_min=0&salary_max=1000000')).values_list('pk',flat=True)),{self.usd.pk,self.nok.pk,self.unknown.pk})
    def test_nonfinite_bounds_are_ignored(self):
        self.assertEqual(JobService.get_filtered_jobs(QueryDict('salary_min=inf&salary_max=NaN')).count(),3)
