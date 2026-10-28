import json
from unittest.mock import Mock, patch
from django.test import TestCase
from django.utils import timezone
from jobs.models import Job, JobSlugRedirect, Organization
from jobs.services.seo_identity import IMPACT_PAGE_ALIASES, IMPACT_CANONICAL_SLUG, canonical_job


class SeoIdentityTests(TestCase):
    def setUp(self):
        provider = patch('allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_provider',
                         return_value=Mock(get_login_url=Mock(return_value='/test-login/')))
        provider.start(); self.addCleanup(provider.stop)
        self.org = Organization.objects.create(name='Example Employer', slug='identity-employer')

    def job(self, slug, **kwargs):
        values = dict(title='Programme Manager', organization=self.org, location='Remote', country='Global',
                      description='Manage remote impact programmes. ' * 20, source='ashby',
                      application_url='https://example.org/jobs/requisition-123', is_active=True, posted_at=timezone.now())
        values.update(kwargs)
        return Job.objects.create(slug=slug, **values)

    def test_all_live_pages_keep_schema_without_inventing_country(self):
        job = self.job('remote-with-unknown-country')
        response = self.client.get(job.get_absolute_url())
        self.assertContains(response, '"@type": "JobPosting"')
        data = json.loads(response.context['job_posting_schema_json'])
        self.assertNotIn('applicantLocationRequirements', data)
        job.is_active = False; job.save()
        response = self.client.get(job.get_absolute_url())
        self.assertNotContains(response, '"@type": "JobPosting"')

    def test_seven_aliases_redirect_directly_and_preserve_query(self):
        for slug in IMPACT_PAGE_ALIASES:
            response = self.client.get(f'/impact/{slug}/?utm_source=test&page=2')
            self.assertEqual(response.status_code, 301)
            self.assertEqual(response['Location'],f'/impact/{IMPACT_CANONICAL_SLUG}/?utm_source=test&page=2')
        self.assertEqual(self.client.get(f'/impact/{IMPACT_CANONICAL_SLUG}/').status_code, 200)

    def test_aliases_removed_from_browse_and_sitemap_config(self):
        from jobs.seo_config import KEYWORD_SEO_PAGES
        self.assertEqual([v['slug'] for v in KEYWORD_SEO_PAGES if v['filter_type']=='all'],[IMPACT_CANONICAL_SLUG])
        response = self.client.get('/browse/')
        for slug in IMPACT_PAGE_ALIASES:
            self.assertNotContains(response,f'/impact/{slug}/')

    def test_copy_canonical_and_schema_agree_with_original(self):
        original=self.job('programme-manager')
        copy=self.job('programme-manager-1',application_url=original.application_url+'?utm_source=board')
        response=self.client.get(copy.get_absolute_url())
        canonical='https://remoteimpact.org'+original.get_absolute_url()
        self.assertContains(response,f'rel="canonical" href="{canonical}"')
        data=json.loads(response.context['job_posting_schema_json'])
        self.assertEqual(data['url'],canonical)
        self.assertEqual(data['identifier']['value'],str(original.pk))

    def test_numeric_suffix_different_requisition_or_country_is_not_a_duplicate(self):
        self.job('programme-manager')
        separate=self.job('programme-manager-1',application_url='https://example.org/jobs/requisition-456')
        self.assertEqual(canonical_job(separate).pk,separate.pk)
        regional=self.job('programme-manager-2',location='Remote (US)',country='USA')
        self.assertEqual(canonical_job(regional).pk,regional.pk)

    def test_reviewed_cross_source_copy_uses_live_original_only(self):
        original=self.job('programme-manager')
        copy=self.job('programme-manager-1',application_url='https://other-board.org/jobs/345')
        JobSlugRedirect.objects.create(old_slug=copy.slug,job=original)
        self.assertEqual(canonical_job(copy).pk,original.pk)
        original.is_active=False;original.save()
        self.assertEqual(canonical_job(copy).pk,copy.pk)

    def test_job_sitemap_omits_only_reviewed_alias_with_live_target(self):
        from jobs.sitemaps import JobSitemap
        original = self.job('sitemap-original')
        alias = self.job('sitemap-alias', application_url='https://other.example/jobs/123')
        separate = self.job('sitemap-separate', application_url='https://example.org/jobs/456')
        JobSlugRedirect.objects.create(old_slug=alias.slug, job=original)
        slugs = set(JobSitemap().items().values_list('slug', flat=True))
        self.assertIn(original.slug, slugs)
        self.assertIn(separate.slug, slugs)
        self.assertNotIn(alias.slug, slugs)
        original.is_active = False
        original.save()
        self.assertIn(alias.slug, set(JobSitemap().items().values_list('slug', flat=True)))

    def test_job_sitemap_keeps_self_mapping(self):
        from jobs.sitemaps import JobSitemap
        job = self.job('sitemap-self')
        JobSlugRedirect.objects.create(old_slug=job.slug, job=job)
        self.assertIn(job.slug, set(JobSitemap().items().values_list('slug', flat=True)))

    def test_public_sitemap_shards_and_count_use_reviewed_canonical_inventory(self):
        from django.core.cache.backends.locmem import LocMemCache
        original = self.job('public-original')
        alias = self.job('public-alias', application_url='https://other.example/jobs/123')
        separate = self.job('public-separate', application_url='https://example.org/jobs/456')
        JobSlugRedirect.objects.create(old_slug=alias.slug, job=original)
        isolated_cache = LocMemCache('public-sitemap-test', {})
        with patch('jobboard.urls.cache', isolated_cache), patch('jobboard.urls.JOB_SITEMAP_PAGE_SIZE', 2):
            index = self.client.get('/sitemap.xml')
            self.assertContains(index, '/sitemap-jobs-1.xml')
            self.assertNotContains(index, '/sitemap-jobs-2.xml')
            shard = self.client.get('/sitemap-jobs-1.xml')
            self.assertContains(shard, original.get_absolute_url())
            self.assertContains(shard, separate.get_absolute_url())
            self.assertNotContains(shard, alias.get_absolute_url())
            original.is_active = False
            original.save()
            isolated_cache.clear()
            shard = self.client.get('/sitemap-jobs-1.xml')
            self.assertContains(shard, alias.get_absolute_url())
            self.assertNotContains(shard, original.get_absolute_url())
