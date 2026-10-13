from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.utils import timezone

from jobs.models import Job, Organization
from jobs.views.organizations import OrganizationDirectoryView


class OrganizationDirectoryInventoryTests(TestCase):
    def job(self, org, slug, **kwargs):
        return Job.objects.create(
            organization=org, slug=slug, title=slug, description='Role description',
            source=Job.Source.MANUAL, external_id=slug,
            application_url='https://example.org/jobs/' + slug, **kwargs,
        )

    def directory(self, query=''):
        view = OrganizationDirectoryView()
        view.request = RequestFactory().get('/organizations/', {'q': query})
        return list(view.get_queryset())

    def test_expired_sibling_does_not_hide_employer_or_inflate_count(self):
        org = Organization.objects.create(name='Climate Team', slug='climate-team')
        self.job(org, 'open')
        self.job(org, 'expired', expires_at=timezone.now() - timedelta(days=1))
        self.job(org, 'inactive', is_active=False)
        rows = self.directory()
        self.assertEqual([(r.pk, r.active_job_count) for r in rows], [(org.pk, 1)])

    def test_directory_agrees_with_public_inventory_policy(self):
        org = Organization.objects.create(name='Climate Team', slug='climate-team')
        self.job(org, 'quality-excluded', quality_score=20)
        old = self.job(org, 'stale')
        Job.objects.filter(pk=old.pk).update(posted_at=timezone.now() - timedelta(days=400))
        rejected = Organization.objects.create(
            name='Rejected', slug='rejected',
            verification_status=Organization.VerificationStatus.REJECTED,
        )
        self.job(rejected, 'rejected-role')
        self.assertEqual(self.directory(), [])

    def test_redirect_alias_excluded_and_search_preserved(self):
        alias = Organization.objects.create(name='FAR AI alias', slug='farai-1')
        canonical = Organization.objects.create(name='FAR AI', slug='farai')
        self.job(alias, 'alias-role')
        self.job(canonical, 'canonical-role')
        self.assertEqual([r.pk for r in self.directory('FAR')], [canonical.pk])
        self.assertEqual(self.directory('unmatched'), [])
