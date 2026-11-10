from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import Category, RoundupSubscriber
from jobs.services.roundup import active_subscribers
from jobs.tests.test_employer_product import EmployerTestBase

ORIGIN = "https://remoteimpact.org"


@override_settings(SECURE_SSL_REDIRECT=False, SITE_URL=ORIGIN)
class WeeklyApplyPromptTests(EmployerTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.job.category = Category.objects.create(name="Climate & Environment", slug="climate-environment")
        self.job.save()
        self.send = patch("jobs.views.roundup.email_service.send_email", return_value=True).start()
        self.addCleanup(patch.stopall)
        self.quick = reverse("jobs:roundup_quick")

    def signup(self, origin=ORIGIN, **data):
        payload = {"email": "Reader@Example.org", "consent": "yes", "topic": "climate",
                   "source": "apply_prompt", "campaign": "climate-environment", **data}
        return self.client.post(self.quick, payload, HTTP_ORIGIN=origin)

    def job_page(self):
        return self.client.get(reverse("jobs:job_detail", kwargs={"slug": self.job.slug}))

    def test_signup_from_the_prompt_requests_confirmation_with_attribution(self):
        response = self.signup()
        self.assertEqual(response.json(), {"requested": True})
        sub = RoundupSubscriber.objects.get(email="reader@example.org")
        self.assertEqual((sub.source, sub.campaign, sub.topic), ("apply_prompt", "climate-environment", "climate"))
        self.assertEqual(self.send.call_args.args[0], "reader@example.org")
        self.assertNotIn(sub, active_subscribers())

    def test_other_origins_cannot_sign_people_up(self):
        self.assertEqual(self.signup(origin="https://evil.example").status_code, 403)
        self.assertEqual(self.client.post(self.quick, {"email": "a@example.org", "consent": "yes"}).status_code, 403)
        self.assertFalse(RoundupSubscriber.objects.exists())
        self.send.assert_not_called()

    def test_signup_needs_consent_and_a_real_address(self):
        self.assertEqual(self.signup(consent="").status_code, 400)
        response = self.signup(email="not-an-email")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Enter a valid email address.")
        self.assertFalse(RoundupSubscriber.objects.exists())

    def test_unknown_topics_and_labels_fall_back_safely(self):
        self.signup(topic="<script>", campaign="https://x.example/?q=1")
        sub = RoundupSubscriber.objects.get()
        self.assertEqual((sub.topic, sub.campaign), ("all", ""))

    def test_job_page_offers_the_prompt_for_its_topic(self):
        response = self.job_page()
        self.assertContains(response, 'id="weekly-apply-prompt"')
        self.assertContains(response, 'data-topic="climate"')
        self.assertContains(response, "hand-picked remote climate jobs")

    def test_job_page_offers_the_signup_beside_the_apply_button_before_any_click(self):
        html = self.job_page().content.decode()
        panel = html[html.index('id="apply-panel"'):]
        form = panel.index('data-source="apply_inline"')
        self.assertLess(panel.index('Apply on company site'), form)
        self.assertLess(form, panel.index('Application guide'))

    def test_inline_signups_count_toward_the_daily_job_page_signups(self):
        from jobs.services.impact_metrics import day_signal
        self.signup(source="apply_inline")
        self.signup(email="second@example.org")
        self.assertEqual(day_signal(timezone.localdate())["weekly_requests_from_apply_prompt"], 2)

    def test_subscribed_members_are_not_prompted(self):
        user = self.make_user()
        RoundupSubscriber.objects.create(email=user.email, confirmed_at=timezone.now())
        self.client.force_login(user)
        page = self.job_page()
        self.assertNotContains(page, 'id="weekly-apply-prompt"')
        self.assertNotContains(page, "data-weekly-inline")

    def test_signed_in_members_who_are_not_subscribed_see_their_email_prefilled(self):
        self.client.force_login(self.make_user())
        self.assertContains(self.job_page(), 'value="member@example.org"')

    def test_roles_without_an_external_apply_link_have_no_prompt(self):
        self.job.application_url = ""
        self.job.application_email = "jobs@example.org"
        self.job.save()
        page = self.job_page()
        self.assertNotContains(page, 'id="weekly-apply-prompt"')
        self.assertNotContains(page, "data-weekly-inline")

    def make_user(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user("member", "member@example.org", "pw")
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        return user
