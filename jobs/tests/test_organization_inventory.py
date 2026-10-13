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


class OrganizationProfileMetadataTests(TestCase):
    def context(self, org):
        from jobs.views.organizations import OrganizationProfileView
        view = OrganizationProfileView()
        view.setup(RequestFactory().get('/organizations/' + org.slug + '/'), slug=org.slug)
        view.object_list = view.get_queryset()
        return view.get_context_data()

    def test_categories_and_metadata_only_describe_available_jobs(self):
        from jobs.models import Category
        org = Organization.objects.create(name='Research Team', slug='research-team')
        open_area = Category.objects.create(name='Climate', slug='climate')
        closed_area = Category.objects.create(name='Education', slug='education')
        for slug, category, active in [('open', open_area, True), ('closed', closed_area, False)]:
            Job.objects.create(organization=org, title=slug, slug=slug, category=category,
                               source=Job.Source.MANUAL, is_active=active)
        changed = timezone.now() - timedelta(days=8)
        Job.objects.filter(organization=org).update(updated_at=changed)
        context = self.context(org)
        self.assertEqual(list(context['categories']), [open_area])
        self.assertEqual(context['job_count'], 1)
        self.assertEqual(context['date_modified'], changed.isoformat())
        self.assertIn('Climate', context['meta_description'])
        self.assertNotIn('checked daily', context['meta_description'])

    def test_empty_target_employer_does_not_advertise_unverified_internships(self):
        org = Organization.objects.create(name='Carnegie Endowment for International Peace',
                                         slug='carnegie-endowment-for-international-peace')
        context = self.context(org)
        self.assertFalse(context['index_page'])
        self.assertIn('no open remote roles', context['meta_description'])
        self.assertNotIn('Apply now', context['meta_description'])
        self.assertNotIn('Internships', context['meta_title'])
        self.assertIsNone(context['date_modified'])
