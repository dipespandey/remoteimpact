from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from jobs.link_models import LinkProspect
from jobs.services import link_prospecting as service
from jobs.views.admin_backlinks import WorkspaceForm


class BacklinkWorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("workspace-reviewer", "staff@example.org", "test-only")
        self.client.force_login(self.user)
        self.prospect = LinkProspect.objects.create(name="Health resources", url="https://university.example/careers/", sector="health",
            audience="graduates", fit_reason="Remote roles complement the job boards listed here.",
            target_url="https://remoteimpact.org/jobs/category/global-health/", contact_email="careers@university.example",
            contact_source_url="https://university.example/contact/")

    def form_data(self, prospect=None):
        prospect = prospect or self.prospect
        form = WorkspaceForm(instance=prospect)
        return {name: form[name].value() if form[name].value() is not None else "" for name in form.fields}

    def test_dedicated_page_shows_cards_and_links_from_outreach(self):
        response = self.client.get(reverse("admin_backlinks"))
        self.assertContains(response, "Backlink outreach")
        self.assertContains(response, f'id="prospect-{self.prospect.pk}"')
        self.assertContains(response, "Generate pitch")
        self.assertNotContains(response, "changelist-form")
        response = self.client.get(reverse("admin_outreach"))
        self.assertContains(response, reverse("admin_backlinks"))

    def test_view_permission_does_not_allow_edits_or_workflow(self):
        staff = get_user_model().objects.create_user("workspace-reader", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("admin_backlinks")).status_code, 403)
        staff.user_permissions.add(Permission.objects.get(codename="view_linkprospect"))
        response = self.client.get(reverse("admin_backlinks"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Generate pitch")
        self.assertEqual(self.client.post(reverse("admin_backlinks_update", args=[self.prospect.pk]), self.form_data()).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin_backlinks_seed")).status_code, 403)

    def test_filters_and_due_followups(self):
        LinkProspect.objects.create(name="Climate", url="https://climate.example/resources/", sector="climate", status="contacted",
            follow_up_on=timezone.localdate() - timedelta(days=1))
        response = self.client.get(reverse("admin_backlinks"), {"sector": "climate", "due": "1"})
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["prospect"].name, "Climate")
        self.assertEqual(response.context["pipeline"]["due"], 1)

    def test_inline_draft_and_review_preserve_filter_values(self):
        data = self.form_data()
        data.update({"operation": "draft", "_filter_sector": "", "_filter_status": "", "next": "https://evil.example"})
        response = self.client.post(reverse("admin_backlinks_update", args=[self.prospect.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin_backlinks") + f"#prospect-{self.prospect.pk}")
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.status, "draft")
        data = self.form_data()
        data["operation"] = "approve"
        self.client.post(reverse("admin_backlinks_update", args=[self.prospect.pk]), data)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.status, "approved")
        response = self.client.get(reverse("admin_backlinks"))
        self.assertContains(response, "Open in mail")
        data = self.form_data()
        data.update({"pitch": "Changed after review", "operation": "save"})
        self.client.post(reverse("admin_backlinks_update", args=[self.prospect.pk]), data)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.status, "draft")
        self.assertIsNone(self.prospect.reviewed_at)

    def test_invalid_input_keeps_form_data_without_writing(self):
        data = self.form_data()
        data.update({"url": "javascript:alert(1)", "operation": "save"})
        response = self.client.post(reverse("admin_backlinks_update", args=[self.prospect.pk]), data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["rows"][0]["form"].errors)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.url, "https://university.example/careers/")

    def test_create_and_seed_return_to_dedicated_workspace(self):
        data = {"name": "New partner", "url": "https://partner.example/resources/", "sector": "climate", "kind": "community", "priority": 1}
        response = self.client.post(reverse("admin_backlinks_create"), data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("admin_backlinks")))
        self.assertTrue(LinkProspect.objects.filter(name="New partner", status="research").exists())
        response = self.client.post(reverse("admin_backlinks_seed"))
        self.assertEqual(response.url, reverse("admin_backlinks"))

    def test_mutations_are_post_only_and_csrf_protected(self):
        url = reverse("admin_backlinks_update", args=[self.prospect.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(url, self.form_data()).status_code, 403)

    def test_page_quality_filter_and_edit_invalidates_old_assessment(self):
        self.prospect.research_assessment = {'status':'promising','checked_on':'2026-10-30','score':75,
            'reasons':['<script>untrusted</script>'], 'permission':'Not established'}
        self.prospect.save()
        response = self.client.get(reverse('admin_backlinks'), {'quality':'promising'})
        self.assertEqual(len(response.context['rows']), 1)
        self.assertContains(response, '&lt;script&gt;untrusted&lt;/script&gt;')
        response = self.client.get(reverse('admin_backlinks'), {'quality':'unsuitable'})
        self.assertEqual(len(response.context['rows']), 0)
        data = self.form_data()
        data.update(url='https://different.example/resources/', operation='save', _filter_quality='promising')
        response = self.client.post(reverse('admin_backlinks_update', args=[self.prospect.pk]), data)
        self.assertIn('quality=promising', response.url)
        self.prospect.refresh_from_db()
        self.assertEqual(self.prospect.research_assessment, {})
        self.assertIsNone(self.prospect.last_checked_at)
