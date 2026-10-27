from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from jobs.models import UserProfile


class AdminWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser("workspace-owner", "owner@example.org", "test-only")
        cls.reader = get_user_model().objects.create_user("profile-reader", is_staff=True)
        cls.reader.user_permissions.add(Permission.objects.get(codename="view_userprofile"))
        cls.person = get_user_model().objects.create_user("seeker-name", "seeker@example.org")
        cls.profile, _ = UserProfile.objects.get_or_create(user=cls.person)
        cls.profile.headline = '<script>alert("unsafe")</script>' + " A long headline" * 8
        cls.profile.save()

    def test_home_groups_daily_work_and_keeps_full_registry_separate(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Manage the work behind Remote Impact.")
        self.assertContains(response, "Job Scouts")
        self.assertContains(response, reverse("admin:jobs_roundupsubscriber_changelist"))
        self.assertContains(response, reverse("admin:jobs_consumerfunnelevent_changelist"))
        self.assertContains(response, reverse("admin:jobs_employerfunnelevent_changelist"))
        self.assertContains(response, "Customer funnel events")
        self.assertContains(response, "Employer funnel events")
        self.assertNotContains(response, reverse("admin:mfa_authenticator_changelist"))
        self.assertContains(response, reverse("admin:records"))
        response = self.client.get(reverse("admin:records"))
        self.assertContains(response, reverse("admin:jobs_consumerfunnelevent_changelist"))
        self.assertContains(response, reverse("admin:mfa_authenticator_changelist"))
        self.assertContains(response, 'id="ri-record-search"')

    def test_registry_and_primary_navigation_respect_model_permissions(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, reverse("admin:jobs_userprofile_changelist"))
        self.assertNotContains(response, reverse("admin:auth_user_changelist"))
        self.assertNotContains(response, reverse("admin:jobs_job_changelist"))
        self.assertNotContains(response, reverse("admin:jobs_consumerfunnelevent_changelist"))
        self.assertNotContains(response, reverse("admin:jobs_employerfunnelevent_changelist"))
        self.assertNotContains(response, reverse("admin_diagnostics"))
        self.assertNotContains(response, reverse("admin_backlinks"))
        response = self.client.get(reverse("admin:records"))
        self.assertContains(response, reverse("admin:jobs_userprofile_changelist"))
        self.assertNotContains(response, reverse("admin:jobs_consumerfunnelevent_changelist"))
        self.assertEqual(self.client.get(reverse("admin:jobs_consumerfunnelevent_changelist")).status_code, 403)

    def test_anonymous_and_nonstaff_cannot_access_registry(self):
        for user in (None, self.person):
            if user:
                self.client.force_login(user)
            response = self.client.get(reverse("admin:records"))
            self.assertEqual(response.status_code, 302)
            self.assertNotContains(response, "Consumer funnel events", status_code=302)

    def test_profile_search_names_links_and_safe_full_headline(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("admin:jobs_userprofile_changelist"), {"q": "seeker-name"})
        self.assertContains(response, reverse("admin:auth_user_change", args=[self.person.pk]))
        self.assertContains(response, reverse("admin:jobs_userprofile_change", args=[self.profile.pk]))
        self.assertContains(response, "seeker@example.org")
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("unsafe")</script>')
        self.assertNotContains(response, "Newsletter preference (legacy)")
        self.assertEqual(response.context["cl"].result_count, 1)

    def test_profile_reader_sees_profile_but_not_privileged_account_link(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse("admin:jobs_userprofile_changelist"))
        self.assertContains(response, "seeker-name")
        self.assertNotContains(response, reverse("admin:auth_user_change", args=[self.person.pk]))
        self.assertNotContains(response, "seeker@example.org")
        self.assertContains(response, reverse("admin:jobs_userprofile_change", args=[self.profile.pk]))
        # Object-level denials still win even for a normally privileged user.
        self.client.force_login(self.owner)
        with patch.object(admin.site._registry[get_user_model()], "has_view_or_change_permission", return_value=False):
            response = self.client.get(reverse("admin:jobs_userprofile_changelist"))
        self.assertNotContains(response, reverse("admin:auth_user_change", args=[self.person.pk]))

    def test_forms_history_and_direct_technical_urls_are_still_available(self):
        self.client.force_login(self.owner)
        for url in (
            reverse("admin:jobs_userprofile_change", args=[self.profile.pk]),
            reverse("admin:jobs_userprofile_history", args=[self.profile.pk]),
            reverse("admin:jobs_jobalertdelivery_changelist"),
            reverse("admin:password_change"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.get(reverse("admin:jobs_userprofile_change", args=[self.profile.pk]), {"_popup": "1"})
        self.assertNotContains(response, 'class="ri-sidebar"')

    def test_app_index_still_exposes_all_permitted_models_in_that_app(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("admin:app_list", args=["jobs"]))
        self.assertContains(response, reverse("admin:jobs_consumerfunnelevent_changelist"))
        self.assertNotContains(response, reverse("admin:mfa_authenticator_changelist"))

    def test_profile_rows_do_not_fetch_users_one_by_one(self):
        model_admin = admin.site._registry[UserProfile]
        from django.test import RequestFactory
        request = RequestFactory().get(reverse("admin:jobs_userprofile_changelist"))
        request.user = self.owner
        changelist = model_admin.get_changelist_instance(request)
        account_column = model_admin.get_list_display(request)[0]
        with self.assertNumQueries(1):
            for obj in changelist.queryset:
                account_column(obj)
