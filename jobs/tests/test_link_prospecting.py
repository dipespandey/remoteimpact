import csv
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from jobs.link_admin import ProspectForm
from jobs.link_models import LinkProspect
from jobs.services import link_prospecting as service
from jobs.services.safe_fetch import FetchResult


@override_settings(SITE_URL="https://remoteimpact.org")
class LinkProspectingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("reviewer", "reviewer@example.org", "test-only")
        self.prospect = LinkProspect.objects.create(
            name="University careers", url="https://university.example/careers/", sector="health",
            audience="global health graduates", fit_reason="Your readers could use a current source of remote global-health roles.",
            target_url="https://remoteimpact.org/jobs/category/global-health/",
            contact_email="careers@university.example", contact_source_url="https://university.example/contact/",
        )
        self.client.force_login(self.user)

    def test_import_deduplicates_without_overwriting_opt_out(self):
        self.prospect.status = "do_not_contact"
        self.prospect.save()
        prospect, created = service.import_prospect({"url": "https://UNIVERSITY.example/careers/#jobs", "name": "Overwrite"})
        self.assertFalse(created)
        self.assertEqual(prospect.pk, self.prospect.pk)
        self.assertEqual(prospect.status, "do_not_contact")
        self.assertEqual(prospect.name, "University careers")

    def test_review_and_contact_require_complete_current_draft(self):
        with self.assertRaises(ValidationError):
            service.record_contact(self.prospect, self.user)
        service.generate_pitch(self.prospect)
        self.prospect.contact_source_url = ""
        self.prospect.save()
        with self.assertRaises(ValidationError):
            service.approve(self.prospect, self.user)
        self.prospect.contact_source_url = "https://university.example/contact/"
        self.prospect.save()
        service.approve(self.prospect, self.user)
        service.record_contact(self.prospect, self.user)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.status, "contacted")
        self.assertIsNotNone(self.prospect.follow_up_on)
        self.assertEqual(self.prospect.events.filter(action="contact_recorded").count(), 1)
        with self.assertRaises(ValidationError):
            service.record_contact(self.prospect, self.user)

    def test_draft_refuses_opt_out_and_unsourced_fit(self):
        self.prospect.audience = ""
        with self.assertRaises(ValidationError):
            service.generate_pitch(self.prospect)
        self.prospect.audience = "graduates"
        self.prospect.status = "do_not_contact"
        with self.assertRaises(ValidationError):
            service.generate_pitch(self.prospect)
        self.assertEqual(self.prospect.pitch, "")

    def test_draft_does_not_invent_contacts_or_metrics(self):
        self.prospect.contact_email = ""
        service.generate_pitch(self.prospect)
        self.assertEqual(self.prospect.contact_email, "")
        self.assertIn(self.prospect.fit_reason, self.prospect.pitch)
        self.assertIn(self.prospect.url, self.prospect.pitch)
        self.assertNotIn("3,626", self.prospect.pitch)

    @patch("jobs.services.link_prospecting.fetch_public_html")
    def test_inspection_records_sourced_candidates_without_adopting_them(self, fetch):
        fetch.return_value = FetchResult("ok", text='<title>Careers</title><a href="mailto:editor@example.org">Email</a><a href="/contact/">Contact</a>')
        old_contact = self.prospect.contact_email
        service.inspect_page(self.prospect)
        self.assertEqual(self.prospect.contact_email, old_contact)
        self.assertEqual(self.prospect.contact_candidates[0]["source_url"], self.prospect.url)
        self.assertEqual(self.prospect.contact_candidates[1]["url"], "https://university.example/contact/")

    @patch("jobs.services.link_prospecting.fetch_public_html")
    def test_verification_matches_target_and_retains_rel_and_noindex(self, fetch):
        self.prospect.placement_url = "https://university.example/new-resources/"
        fetch.return_value = FetchResult("ok", text='''<meta name="robots" content="noindex">
            <a href="https://remoteimpact.org/jobs/category/global-health/?utm_source=university#jobs" rel="nofollow sponsored">Remote jobs</a>
            <a href="https://remoteimpact.org.evil.example/jobs/category/global-health/">Fake</a>''')
        self.assertEqual(service.inspect_page(self.prospect, placement=True), "target_link_found")
        self.assertEqual(len(self.prospect.link_evidence), 1)
        self.assertEqual(self.prospect.link_evidence[0]["rel"], ["nofollow", "sponsored"])
        self.assertTrue(self.prospect.link_evidence[0]["page_noindex"])
        self.assertEqual(self.prospect.status, "research")  # Observed link does not assert editorial approval.

    @patch("jobs.services.link_prospecting.fetch_public_html")
    def test_failure_is_unknown_and_previous_observation_remains_in_history(self, fetch):
        self.prospect.placement_url = "https://university.example/resources/"
        fetch.return_value = FetchResult("ok", text=f'<a href="{self.prospect.target_url}">Jobs</a>')
        service.inspect_page(self.prospect, placement=True)
        fetch.return_value = FetchResult("http_403", status_code=403)
        self.assertEqual(service.inspect_page(self.prospect, placement=True), "http_403")
        self.assertEqual(self.prospect.events.count(), 2)
        self.assertIn('"matches_target": true', self.prospect.events.order_by("created_at").first().detail)

    @patch("jobs.services.link_prospecting.fetch_public_html")
    def test_relative_links_use_final_page_url(self, fetch):
        fetch.return_value = FetchResult("ok", text='<a href="contact/">Contact</a>', final_url="https://university.example/new-location/")
        service.inspect_page(self.prospect)
        self.assertEqual(self.prospect.contact_candidates[0]["url"], "https://university.example/new-location/contact/")

    @patch("jobs.services.link_prospecting.fetch_public_html")
    def test_redirect_to_our_own_site_is_not_a_backlink(self, fetch):
        self.prospect.placement_url = "https://university.example/redirect/"
        fetch.return_value = FetchResult("ok", text=f'<a href="{self.prospect.target_url}">Jobs</a>', final_url="https://remoteimpact.org/resources/")
        self.assertEqual(service.inspect_page(self.prospect, placement=True), "redirected_to_target_site")

    def test_admin_workflow_requires_csrf(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("admin:jobs_linkprospect_workflow", args=[self.prospect.pk, "draft"])).status_code, 403)

    def test_permissions_and_post_only_actions(self):
        url = reverse("admin:jobs_linkprospect_workflow", args=[self.prospect.pk, "draft"])
        self.assertEqual(self.client.get(url).status_code, 405)
        reader = get_user_model().objects.create_user("reader", is_staff=True)
        reader.user_permissions.add(Permission.objects.get(codename="view_linkprospect"))
        self.client.force_login(reader)
        self.assertEqual(self.client.get(reverse("admin:jobs_linkprospect_changelist")).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:jobs_linkprospect_seed")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("admin:jobs_linkprospect_changelist")).status_code, 302)

    def test_import_is_atomic_on_invalid_row(self):
        content = b"name,url\nGood,https://good.example/careers/\nBad,javascript:alert(1)\n"
        response = self.client.post(reverse("admin:jobs_linkprospect_import"), {"file": SimpleUploadedFile("prospects.csv", content)})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LinkProspect.objects.filter(name="Good").exists())

    def test_seed_and_dry_run_are_idempotent(self):
        call_command("link_prospects", seed=True, dry_run=True, stdout=io.StringIO())
        self.assertEqual(LinkProspect.objects.count(), 1)
        call_command("link_prospects", seed=True, stdout=io.StringIO())
        call_command("link_prospects", seed=True, stdout=io.StringIO())
        self.assertEqual(LinkProspect.objects.count(), 7)

    def test_form_cannot_skip_review_or_report_placement_without_url(self):
        data = {"name": self.prospect.name, "url": self.prospect.url, "sector": "health", "kind": "resource", "priority": 2,
                "status": "contacted"}
        form = ProspectForm(data=data, instance=self.prospect)
        self.assertFalse(form.is_valid())
        self.assertIn("status", form.errors)
        data["status"] = "placed"
        form = ProspectForm(data=data, instance=self.prospect)
        self.assertFalse(form.is_valid())
        self.assertIn("placement_url", form.errors)

    def test_admin_edit_invalidates_review(self):
        service.generate_pitch(self.prospect)
        service.approve(self.prospect, self.user)
        self.prospect.refresh_from_db()
        response = self.client.get(reverse("admin:jobs_linkprospect_change", args=[self.prospect.pk]))
        self.assertEqual(response.status_code, 200)
        form = response.context["adminform"].form
        data = {name: form[name].value() if form[name].value() is not None else "" for name in form.fields}
        data.update({"pitch": "Edited pitch needs review", "_save": "Save", "events-TOTAL_FORMS": "0", "events-INITIAL_FORMS": "0",
                     "events-MIN_NUM_FORMS": "0", "events-MAX_NUM_FORMS": "0"})
        response = self.client.post(reverse("admin:jobs_linkprospect_change", args=[self.prospect.pk]), data)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.status, "draft")
        self.assertIsNone(self.prospect.reviewed_at)

    def test_browser_line_endings_do_not_invalidate_review(self):
        service.generate_pitch(self.prospect)
        service.approve(self.prospect, self.user)
        self.prospect.refresh_from_db()
        response = self.client.get(reverse("admin:jobs_linkprospect_change", args=[self.prospect.pk]))
        form = response.context["adminform"].form
        data = {name: form[name].value() if form[name].value() is not None else "" for name in form.fields}
        data.update({"pitch": self.prospect.pitch.replace("\n", "\r\n"), "_workflow": "contact",
                     "events-TOTAL_FORMS": "0", "events-INITIAL_FORMS": "0", "events-MIN_NUM_FORMS": "0", "events-MAX_NUM_FORMS": "0"})
        response = self.client.post(reverse("admin:jobs_linkprospect_change", args=[self.prospect.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.status, "contacted")

    def test_export_neutralises_spreadsheet_formulas(self):
        from django.contrib import admin
        from django.test import RequestFactory
        self.prospect.name = "=HYPERLINK(\"https://evil.example\")"
        self.prospect.save()
        request = RequestFactory().get("/")
        response = admin.site._registry[LinkProspect].export_csv(request, LinkProspect.objects.all())
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertTrue(rows[1][0].startswith("'="))

    @patch("ddgs.DDGS")
    def test_discovery_keeps_provenance_and_skips_ads_and_our_site(self, search):
        search.return_value.text.return_value = [
            {"title": "Advert", "href": "https://www.bing.com/aclick?ad=1"},
            {"title": "Us", "href": "https://remoteimpact.org/jobs/"},
            {"title": "New resources", "href": "https://new-university.example/resources/"},
        ]
        call_command("link_prospects", query="climate careers resources", sector="climate", limit=5, stdout=io.StringIO())
        row = LinkProspect.objects.get(name="New resources")
        self.assertEqual(row.source, "DDGS query: climate careers resources")
        self.assertEqual(row.status, "research")
        self.assertEqual(row.contact_email, "")
        self.assertEqual(LinkProspect.objects.count(), 2)

    def test_campaign_links_keep_job_filters_and_match_placement_targets(self):
        self.prospect.target_url += "?country=NO&utm_source=old#jobs"
        url = self.prospect.campaign_url
        self.assertIn(f"utm_source=link_prospect_{self.prospect.pk}", url)
        self.assertIn("country=NO", url)
        self.assertNotIn("utm_source=old", url)
        self.assertEqual(service.comparison_url(url), service.comparison_url(self.prospect.target_url))

    def test_recorded_zero_metrics_require_a_source_and_reporting_window(self):
        from datetime import date
        self.prospect.referral_visits = 0
        with self.assertRaises(ValidationError):
            self.prospect.full_clean()
        self.prospect.metrics_source = "Analytics campaign export"
        self.prospect.metrics_since = date(2026, 10, 1)
        self.prospect.metrics_until = date(2026, 10, 13)
        self.prospect.full_clean()
        self.prospect.save()
        response = self.client.get(reverse("admin:jobs_linkprospect_changelist"))
        self.assertEqual(response.context["pipeline"]["visits"], 0)
        self.assertIsNone(response.context["pipeline"]["signups"])
