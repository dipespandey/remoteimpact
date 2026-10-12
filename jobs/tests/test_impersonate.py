from django.contrib.auth import get_user_model
from django.test import TestCase


class ImpersonateTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user("boss", "boss@example.com", "pw", is_staff=True, is_superuser=True)
        self.seeker = User.objects.create_user("seeker", "seeker@example.com", "pw")
        self.other_staff = User.objects.create_user("ops", "ops@example.com", "pw", is_staff=True)
        self.client.force_login(self.staff)

    def test_view_as_user_is_read_only_and_reversible(self):
        response = self.client.post(f"/admin/impersonate/{self.seeker.pk}/start/", {"next": "/"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.seeker.pk)
        self.assertEqual(self.client.session["impersonator_id"], self.staff.pk)
        self.assertEqual(self.client.post("/accounts/logout/").status_code, 403)
        self.client.post("/admin/impersonate/stop/")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.staff.pk)
        self.assertNotIn("impersonator_id", self.client.session)

    def test_last_login_untouched(self):
        self.client.post(f"/admin/impersonate/{self.seeker.pk}/start/")
        self.seeker.refresh_from_db()
        self.assertIsNone(self.seeker.last_login)

    def test_staff_cannot_be_impersonated(self):
        response = self.client.post(f"/admin/impersonate/{self.other_staff.pk}/start/")
        self.assertEqual(response.status_code, 403)

    def test_non_staff_cannot_start(self):
        self.client.force_login(self.seeker)
        response = self.client.post(f"/admin/impersonate/{self.staff.pk}/start/")
        self.assertNotEqual(int(self.client.session["_auth_user_id"]), self.staff.pk)
        self.assertIn(response.status_code, (302, 403))

    def test_search_lists_only_non_staff(self):
        response = self.client.get("/admin/impersonate/?q=example.com")
        self.assertContains(response, "seeker@example.com")
        self.assertNotContains(response, "ops@example.com")
