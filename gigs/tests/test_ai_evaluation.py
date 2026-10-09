from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse

from gigs.models import EvaluatorInterest


class AIEvaluationWaitlistTests(TestCase):
    def setUp(self):
        site = Site.objects.get_current()
        for provider, provider_id in (("google", ""), ("openid_connect", "linkedin")):
            app = SocialApp.objects.create(provider=provider, provider_id=provider_id, name=provider_id or provider,
                                           client_id="x", secret="x",
                                           settings={"server_url": "https://www.linkedin.com/oauth"} if provider_id else {})
            app.sites.add(site)
        self.url = reverse("gigs:ai_evaluation")
        self.data = {"email": "Sita@Example.org", "country": "Nepal", "languages": "Nepali, Maithili",
                     "background": "health", "hours_per_week": 8, "note": "", "is_adult": "on"}

    def test_page_renders_with_commitments(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Paid AI evaluation work in your language")
        self.assertContains(response, "pay floor")

    def test_adult_signup_is_stored_once(self):
        self.client.post(self.url, self.data)
        self.client.post(self.url, {**self.data, "hours_per_week": 12})
        entry = EvaluatorInterest.objects.get()
        self.assertEqual((entry.email, entry.hours_per_week, entry.is_adult), ("sita@example.org", 12, True))

    def test_minors_and_bad_hours_are_refused(self):
        response = self.client.post(self.url, {**self.data, "is_adult": ""})
        self.assertContains(response, "only open to adults")
        self.client.post(self.url, {**self.data, "hours_per_week": 80})
        self.assertFalse(EvaluatorInterest.objects.exists())
