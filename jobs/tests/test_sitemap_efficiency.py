from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from jobs.models import Category, Job, Organization
from jobs.sitemaps import CategorySitemap, OrganizationSitemap, RolePagesSitemap, KeywordPagesSitemap


class SitemapEfficiencyTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Climate', slug='climate-environment')
        self.org = Organization.objects.create(name='Impact Employer', slug='impact-employer')
        self.old = timezone.now() - timedelta(days=2)
        for i in range(3):
            job = Job.objects.create(title='Software Engineer', slug=f'engineer-{i}',
                organization=self.org, category=self.category, source='manual')
            Job.objects.filter(pk=job.pk).update(updated_at=self.old)

    def test_organization_lastmod_has_no_per_url_queries_and_excludes_inactive_jobs(self):
        hidden = Job.objects.create(title='Closed role', slug='closed', organization=self.org,
            category=self.category, source='manual', is_active=False)
        for i in range(9):
            org = Organization.objects.create(name=f'Employer {i}', slug=f'employer-{i}')
            Job.objects.create(title='Operations Lead', slug=f'ops-{i}', organization=org, source='manual')
        with CaptureQueriesContext(connection) as queries:
            urls = OrganizationSitemap().get_urls(site=SimpleNamespace(domain='remoteimpact.org'), protocol='https')
        self.assertEqual(len(urls), 10)
        self.assertLessEqual(len(queries), 3)
        employer = next(u for u in urls if u['location'].endswith('/org/impact-employer/'))
        self.assertEqual(employer['lastmod'], self.old)

    def test_category_lastmod_and_count_use_only_public_jobs(self):
        Job.objects.create(title='Hidden', slug='hidden', organization=self.org,
            category=self.category, source='manual', is_active=False)
        with CaptureQueriesContext(connection) as queries:
            sitemap = CategorySitemap()
            categories = list(sitemap.items())
            lastmods = [sitemap.lastmod(c) for c in categories]
        self.assertEqual([c.slug for c in categories], [self.category.slug])
        self.assertEqual(lastmods, [self.old])
        self.assertLessEqual(len(queries), 2)

    def test_redirected_organization_is_not_advertised_as_a_sitemap_destination(self):
        legacy = Organization.objects.create(name='FAR legacy', slug='farai-1')
        current = Organization.objects.create(name='FAR AI', slug='farai')
        for org in [legacy,current]:
            Job.objects.create(title='Researcher',slug='researcher-'+org.slug,organization=org,source='manual')
        sitemap=OrganizationSitemap()
        urls=[sitemap.location(org) for org in sitemap.items()]
        self.assertIn('/org/farai/',urls)
        self.assertNotIn('/org/farai-1/',urls)

    def test_role_lastmod_does_not_use_an_unrelated_newer_job(self):
        Job.objects.create(title='Policy Lead', slug='policy', organization=self.org, source='manual')
        configs=[dict(slug='engineers', patterns=['software engineer'])]
        with patch('jobs.seo_config.ROLE_SEO_PAGES', configs):
            sitemap = RolePagesSitemap()
            with CaptureQueriesContext(connection) as queries:
                item = sitemap.items()[0]
                self.assertEqual(sitemap.lastmod(item), self.old)
                self.assertEqual(sitemap.lastmod(item), self.old)
            self.assertEqual(len(queries), 1)

    def test_keyword_lastmod_uses_its_own_category_and_keeps_sparse_pages_out(self):
        Job.objects.create(title='New unrelated role', slug='unrelated', organization=self.org, source='manual')
        configs=[dict(slug='climate', filter_type='category', category_slug=self.category.slug),
                 dict(slug='empty', filter_type='category', category_slug='missing')]
        with patch('jobs.seo_config.KEYWORD_SEO_PAGES', configs):
            sitemap = KeywordPagesSitemap()
            items = sitemap.items()
            self.assertEqual([i['slug'] for i in items], ['climate'])
            with self.assertNumQueries(0):
                self.assertEqual(sitemap.lastmod(items[0]), self.old)
