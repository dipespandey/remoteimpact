from unittest.mock import patch

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from jobs.models import Job, Organization
from jobs.services.seo_text import job_title_tag


class SeoTitleIdentityTests(SimpleTestCase):
    def test_region_qualifiers_survive_long_employer_and_role_names(self):
        employer='Intertribal Agriculture Council'
        titles=[job_title_tag('Regional Outreach and Education Manager- '+region+' Region',employer) for region in ['NW','SW']]
        self.assertNotEqual(*titles)
        for title,region in zip(titles,['NW','SW']):
            self.assertIn(region+' Region',title)
            self.assertTrue(title.startswith('Regional'))
            self.assertIn(employer, title)

    def test_specializations_are_not_trimmed_to_identical_titles(self):
        roles=['Technical Business Development Strategist – '+specialty for specialty in ['Distribution','EMA','Transmission Planning']]
        titles=[job_title_tag(role,'Electric Power Engineers') for role in roles]
        self.assertEqual(len(set(titles)),3)
        for title, role in zip(titles, roles):
            self.assertIn(role, title)


class EmployerRepetitionTests(SimpleTestCase):
    def test_remove_only_trailing_exact_employer_attribution(self):
        title = job_title_tag("Communications and Media Relations Manager for World BEYOND War", "World BEYOND War")
        self.assertEqual(title.count("World BEYOND War"), 1)
        self.assertIn("Communications", title)
        self.assertEqual(job_title_tag("Analyst at RAND", "RAND"), job_title_tag("Analyst", "RAND"))

    def test_preserve_role_words_and_nonmatching_organizations(self):
        self.assertIn("Advocate for Children", job_title_tag("Advocate for Children", "Acme"))
        self.assertIn("RAND Policy Analyst", job_title_tag("RAND Policy Analyst", "RAND"))
        self.assertIn("Analyst for RAND Europe", job_title_tag("Analyst for RAND Europe", "RAND"))


class RoleWordingTests(SimpleTestCase):
    def test_middle_specializations_remain_distinct(self):
        roles = ["Head of Francophone Africa " + specialty + " and Venture Builder"
                 for specialty in ["Export Manufacturing", "Labour Mobility"]]
        titles = [job_title_tag(role, "Africa Jobs Fund") for role in roles]
        self.assertNotEqual(*titles)
        for role, title in zip(roles, titles):
            self.assertIn(role, title)
            self.assertIn("Africa Jobs Fund", title)


class PostingLocationTitleTests(SimpleTestCase):
    def test_country_restrictions_distinguish_same_role_at_same_employer(self):
        titles = [job_title_tag("Solutions Architecture Lead", "AMPECO", location=place)
                  for place in ["France", "Germany"]]
        self.assertNotEqual(*titles)
        self.assertIn("France", titles[0])
        self.assertIn("Germany", titles[1])

    def test_skip_generic_or_already_named_location(self):
        self.assertEqual(job_title_tag("Analyst", "Acme", location="Remote"), job_title_tag("Analyst", "Acme"))
        self.assertEqual(job_title_tag("Analyst (Remote, US)", "Acme", location="Remote (US)"),
                         job_title_tag("Analyst (Remote, US)", "Acme"))

    def test_short_disambiguator_keeps_same_requisitions_distinct(self):
        entry = job_title_tag("Power Systems Engineer I", "Electric Power Engineers", disambiguator="Entry")
        mid = job_title_tag("Power Systems Engineer I", "Electric Power Engineers", disambiguator="Mid")
        self.assertNotEqual(entry, mid)
        self.assertIn("(Entry)", entry)
        self.assertIn("(Mid)", mid)
        self.assertLessEqual(len(entry), 62)


class JobDetailSeoTitleTests(TestCase):
    def setUp(self):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(
                provider=provider,
                provider_id=provider_id,
                name=provider,
                client_id="test-client",
                secret="test-secret",
                settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {},
            )
            app.sites.add(site)
        for mocked in [
            patch("jobs.services.embedding_service.embed_job", return_value=[0.0] * 384),
            patch("jobs.signals.threading.Thread"),
        ]:
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_duplicate_live_requisitions_get_distinct_title_tags(self):
        org = Organization.objects.create(name="Electric Power Engineers", slug="electric-power-engineers")
        common = {
            "title": "Power Systems Engineer I",
            "organization": org,
            "location": "Remote (US)",
            "description": "Develop and conduct power systems studies.",
            "source": Job.Source.ASHBY,
        }
        entry = Job.objects.create(slug="power-systems-entry", experience_level="entry", **common)
        mid = Job.objects.create(slug="power-systems-mid", experience_level="mid", **common)

        entry_html = self.client.get(reverse("jobs:job_detail", args=[entry.slug])).content.decode()
        mid_html = self.client.get(reverse("jobs:job_detail", args=[mid.slug])).content.decode()

        self.assertIn("Power Systems Engineer I at Electric Power Engineers", entry_html)
        self.assertIn("(Entry)", entry_html)
        self.assertIn("(Mid)", mid_html)
