from django.test import TestCase
from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp

from jobs.models import Category, Job, JobSlugRedirect, Organization


class StandardIconRedirectTests(TestCase):
    def setUp(self):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider,
                provider_id=provider_id,
                name=(provider_id or provider).title(),
                client_id=f"{provider_id or provider}-client",
                secret=f"{provider_id or provider}-secret",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(site)

    def test_standard_browser_icon_aliases_redirect_to_the_declared_favicon(self):
        for path in (
            "/favicon.ico",
            "/apple-touch-icon.png",
            "/apple-touch-icon-precomposed.png",
        ):
            response = self.client.get(path)

            self.assertEqual(response.status_code, 301)
            self.assertEqual(response["Location"], "/static/favicon.svg")

    def test_observed_null_suffixes_return_to_their_canonical_pages(self):
        for path, destination in (
            ("/accounts/login/null", "/accounts/login/"),
            ("/accounts/signup/null", "/accounts/signup/"),
            ("/tools/cover-letter-generator/null", "/tools/cover-letter-generator/"),
            ("/tools/interview-prep/null", "/tools/interview-prep/"),
        ):
            response = self.client.get(path)

            self.assertEqual(response.status_code, 301)
            self.assertEqual(response["Location"], destination)

    def test_not_found_page_recovers_into_job_search_and_scout(self):
        response = self.client.get("/jobs/a-retired-role-that-does-not-exist/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'action="/jobs/"', status_code=404)
        self.assertContains(response, 'name="q"', status_code=404)
        self.assertContains(response, "/?scout=1&amp;source=not_found", status_code=404)
        self.assertContains(response, 'data-track="not_found_search"', status_code=404)
        self.assertContains(response, 'data-track="not_found_job_scout"', status_code=404)

    def test_catalogue_filter_variants_are_not_indexable(self):
        category = Category.objects.create(name="SEO Test", slug="seo-test")

        job_list = self.client.get("/jobs/?organization=example")
        category_list = self.client.get(f"/jobs/category/{category.slug}/?q=remote")

        self.assertContains(job_list, 'name="robots" content="noindex, follow"')
        self.assertContains(category_list, 'name="robots" content="noindex, follow"')

    def test_proved_duplicate_archives_redirect_to_available_canonical_role(self):
        org = Organization.objects.create(name='Employer', slug='canonical-employer')
        keep = Job.objects.create(title='Software Engineer', slug='kept-software-role', organization=org,
            source='ashby', application_url='https://example.org/role', is_active=True)
        old = Job.objects.create(title=keep.title, slug='old-software-role', organization=org,
            source='ashby', application_url=keep.application_url, is_active=False)
        JobSlugRedirect.objects.create(old_slug=old.slug, job=keep)
        response = self.client.get(old.get_absolute_url())
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], keep.get_absolute_url())
        keep.is_active = False
        keep.save(update_fields=['is_active'])
        response = self.client.get(old.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'noindex, follow')

    def test_home_scout_launcher_has_a_single_line_prompt(self):
        response = self.client.get("/")

        self.assertContains(response, "white-space: nowrap")
        self.assertContains(response, "text-overflow: ellipsis")
        self.assertContains(response, "Python in Europe")
        self.assertContains(response, "Climate policy")
