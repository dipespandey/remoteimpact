from datetime import timedelta
from unittest.mock import patch
from xml.etree import ElementTree as ET
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jobs.feeds import LatestJobsFeed, CategoryJobsFeed
from jobs.models import Job, Category, Organization


class SyndicationAvailabilityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Feed employer', slug='feed-employer')
        self.category = Category.objects.create(name='Climate', slug='climate-environment')
        now = timezone.now()
        self.live = self.job('live', last_seen_at=now)
        self.unknown_pay = self.job('unknown-quality', quality_score=0)
        self.job('expired', expires_at=now-timedelta(days=1))
        self.job('stale-import', last_seen_at=now-timedelta(days=31))
        self.job('poor-quality', quality_score=20)
        self.job('inactive', is_active=False)
        rejected = Organization.objects.create(name='Rejected', slug='rejected', verification_status=Organization.VerificationStatus.REJECTED)
        self.job('rejected', organization=rejected)
        self.expected = {self.live.pk, self.unknown_pay.pk}

    def job(self, slug, **kwargs):
        args = dict(organization=self.org, category=self.category, title=slug, slug=slug,
                    source=Job.Source.IDEALIST, description='Source role', application_url='https://example.com/job', quality_score=80)
        args.update(kwargs)
        return Job.objects.create(**args)

    def test_rss_uses_public_availability_and_category_scope(self):
        self.assertEqual({j.pk for j in LatestJobsFeed().items()}, self.expected)
        self.assertEqual({j.pk for j in CategoryJobsFeed().items(self.category)}, self.expected)
        other = Category.objects.create(name='Health', slug='global-health')
        self.assertEqual(list(CategoryJobsFeed().items(other)), [])

    def test_public_xml_and_rss_exclude_unavailable_roles(self):
        for name in ['jobs_feed', 'indeed_feed', 'linkedin_feed']:
            with self.subTest(feed=name):
                response = self.client.get(reverse('jobs:'+name))
                self.assertEqual(response.status_code, 200)
                root = ET.fromstring(response.content)
                if name == 'jobs_feed':
                    links = [i.findtext('link') for i in root.findall('./channel/item')]
                elif name == 'indeed_feed':
                    links = [i.findtext('url') for i in root.findall('job')]
                else:
                    links = [i.findtext('applyUrl') for i in root.findall('job')]
                self.assertEqual({u.rsplit('/', 2)[-2] for u in links}, {'live', 'unknown-quality'})

    def test_rss_job_links_carry_distinct_community_campaigns(self):
        latest = ET.fromstring(self.client.get(reverse('jobs:jobs_feed')).content)
        latest_link = latest.find('./channel/item/link').text
        latest_query = parse_qs(urlsplit(latest_link).query)
        self.assertEqual(latest_query['utm_source'], ['community_rss'])
        self.assertEqual(latest_query['utm_medium'], ['referral'])
        self.assertEqual(latest_query['utm_campaign'], ['latest_jobs_feed'])
        self.assertNotIn('utm_', latest.find('./channel/item/guid').text)

        category = ET.fromstring(
            self.client.get(reverse('jobs:category_feed', kwargs={'slug': self.category.slug})).content
        )
        category_link = category.find('./channel/item/link').text
        category_query = parse_qs(urlsplit(category_link).query)
        self.assertEqual(category_query['utm_source'], ['community_rss'])
        self.assertEqual(category_query['utm_campaign'], ['category_jobs_feed_climate-environment'])
        self.assertNotIn('utm_', category.find('./channel/item/guid').text)

    def test_term_pay_is_not_exported_as_annual_salary(self):
        self.live.description = '$100,000–$167,000 USD for the four-month fellowship (annualized $300,000–$500,000).'
        self.live.salary_min = 300000
        self.live.salary_max = 500000
        self.live.save()
        for feed, fields in [('indeed_feed', ['salary']), ('linkedin_feed', ['salaryMin', 'salaryMax'])]:
            with self.subTest(feed=feed):
                root = ET.fromstring(self.client.get(reverse('jobs:'+feed)).content)
                for item in root.findall('job'):
                    for field in fields:
                        self.assertIsNone(item.find(field))

    def test_api_distinguishes_term_total_from_annual_salary(self):
        from jobs.api.serialize import job_record
        self.live.description = '$100,000–$167,000 USD for the four-month fellowship (annualized $300,000–$500,000).'
        self.live.salary_min = 300000
        self.live.salary_max = 500000
        self.live.raw_data = {'salaryPeriod': 'YEAR', 'salaryCurrency': 'USD'}
        record = job_record(self.live)
        self.assertIsNone(record['salary'])
        self.assertEqual(record['term_compensation']['minimum'], '100000')
        self.assertEqual(record['term_compensation']['duration_value'], 4)

    def test_ordinary_annual_feed_amounts_remain(self):
        self.live.salary_min = 60000
        self.live.salary_max = 80000
        self.live.save()
        root = ET.fromstring(self.client.get(reverse('jobs:linkedin_feed')).content)
        self.assertEqual([j.findtext('salaryMin') for j in root.findall('job') if j.find('salaryMin') is not None], ['60000'])

    def test_annual_salary_comparisons_exclude_annualized_quotes(self):
        from jobs.services.job_service import JobService
        self.live.description = 'Compensation $100,000 for four months; $300,000 annualized.'
        self.live.save()
        ids = set(JobService._annual_salary_jobs(Job.objects.all()).values_list('pk', flat=True))
        self.assertNotIn(self.live.pk, ids)
        self.assertIn(self.unknown_pay.pk, ids)
