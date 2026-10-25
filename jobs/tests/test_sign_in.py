from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(SECURE_SSL_REDIRECT=False, ACCOUNT_RATE_LIMITS={})
class SignInTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.get_current()
        for provider, provider_id in [("google", ""), ("openid_connect", "linkedin")]:
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id,
                name=provider_id or provider, client_id="synthetic", secret="synthetic")
            app.sites.add(site)
        cls.user = get_user_model().objects.create_user(
            "test-member", "member@example.test", "Synthetic-password-753!"
        )

    def test_login_field_accepts_plain_username_and_has_password_manager_hint(self):
        response = self.client.get(reverse("account_login"))
        self.assertContains(response, "Email or username")
        field = response.context["form"].fields["login"]
        self.assertEqual(field.widget.input_type, "text")
        self.assertEqual(field.widget.attrs["autocomplete"], "username")

    def test_both_identifiers_sign_into_same_account_and_preserve_next(self):
        for identifier in (self.user.username, self.user.email):
            with self.subTest(identifier=identifier):
                self.client.logout()
                response = self.client.post(reverse("account_login"), {
                    "login": identifier, "password": "Synthetic-password-753!", "next": "/saved/"
                })
                self.assertRedirects(response, "/saved/", fetch_redirect_response=False)
                self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_wrong_password_does_not_authenticate_either_identifier(self):
        for identifier in (self.user.username, self.user.email, "unknown-member"):
            with self.subTest(identifier=identifier):
                response = self.client.post(reverse("account_login"), {
                    "login": identifier, "password": "incorrect"
                })
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_username_login_still_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("account_login"), {
            "login": self.user.username, "password": "Synthetic-password-753!"
        })
        self.assertEqual(response.status_code, 403)

    @override_settings(ADMIN_REQUIRE_MFA=True)
    def test_staff_username_login_cannot_bypass_mfa_enrollment(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.post(reverse("account_login"), {
            "login": self.user.username, "password": "Synthetic-password-753!", "next": "/admin/"
        })
        response = self.client.get("/admin/")
        self.assertRedirects(response, reverse("mfa_activate_totp"), fetch_redirect_response=False)
