from django.test import RequestFactory, TestCase
from django.template.loader import render_to_string
from django.urls import reverse
from django.contrib.auth import get_user_model
from ..models import UserProfile, Organization, SeekerProfile
from ..services.onboarding_service import OnboardingService
from ..views.impact_wizard import (
    profile_completion_url,
    profile_scout_intent,
    profile_scout_url,
)

User = get_user_model()


class OnboardingServiceTest(TestCase):
    def setUp(self):
        self.password = "pass123"
        self.seeker_user = User.objects.create_user(
            email="seeker@example.com", password=self.password, username="seeker"
        )
        UserProfile.objects.create(
            user=self.seeker_user, account_type=UserProfile.AccountType.SEEKER
        )

        self.employer_user = User.objects.create_user(
            email="employer@example.com", password=self.password, username="employer"
        )
        UserProfile.objects.create(
            user=self.employer_user, account_type=UserProfile.AccountType.EMPLOYER
        )
        self.client.force_login(self.seeker_user)

    def test_seeker_skipped_onboarding(self):
        """
        Seekers should be redirected to account page even if profile is empty.
        get_redirect_for_state returns None for 'onboarding complete' (or skipped)
        """
        # Ensure profile.headline is empty
        self.assertFalse(self.seeker_user.profile.headline)

        redirect_target = OnboardingService.get_redirect_for_state(self.seeker_user)
        self.assertEqual(redirect_target, "jobs:onboarding_seeker")

    def test_seeker_profile_can_be_created_during_onboarding(self):
        profile = SeekerProfile.objects.create(user=self.seeker_user)

        self.assertEqual(profile.user, self.seeker_user)
        self.assertFalse(profile.wizard_completed)

    def test_employer_with_org_skipped_onboarding(self):
        """
        Employers with an existing organization should skip onboarding.
        """
        Organization.objects.create(name="Test Org").members.add(self.employer_user)

        redirect_target = OnboardingService.get_redirect_for_state(self.employer_user)
        self.assertIsNone(
            redirect_target, "Employers with organizations should skip onboarding"
        )

    def test_employer_without_org_needs_onboarding(self):
        """
        Employers without an organization must go to onboarding.
        """
        # Ensure no orgs
        self.assertFalse(self.employer_user.organizations.exists())

        redirect_target = OnboardingService.get_redirect_for_state(self.employer_user)
        self.assertEqual(
            redirect_target,
            "jobs:onboarding_employer",
            "Employers without orgs should be redirected to onboarding",
        )

    def test_onboarding_preserves_and_encodes_safe_next_url(self):
        response = self.client.get(
            reverse("jobs:start_onboarding"),
            {"next": "/alerts/?intent=remote python&source=home_hero"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            "next=%2Falerts%2F%3Fintent%3Dremote+python%26source%3Dhome_hero",
            response.url,
        )

    def test_onboarding_rejects_external_next_url(self):
        response = self.client.get(
            reverse("jobs:start_onboarding"),
            {"next": "https://example.com/steal-session"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("example.com", response.url)

    def test_new_account_starts_with_scout_instead_of_account_type_gate(self):
        new_user = User.objects.create_user(
            email="new-seeker@example.com", password=self.password, username="new-seeker"
        )
        self.client.force_login(new_user)

        response = self.client.get(reverse("jobs:start_onboarding"))

        self.assertRedirects(
            response,
            f"{reverse('jobs:job_alerts')}?source=signup",
            fetch_redirect_response=False,
        )

    def test_scout_signup_next_url_skips_account_type_gate(self):
        new_user = User.objects.create_user(
            email="scout-seeker@example.com", password=self.password, username="scout-seeker"
        )
        self.client.force_login(new_user)
        next_url = "/alerts/?intent=remote+python+roles&source=scout_preview"

        response = self.client.get(reverse("jobs:start_onboarding"), {"next": next_url})

        self.assertRedirects(response, next_url, fetch_redirect_response=False)

    def test_completed_profile_builds_prefilled_scout_brief(self):
        profile = SeekerProfile.objects.create(
            user=self.seeker_user,
            headline="Software engineer",
            skills=["python", "data-analysis"],
            remote_preference=SeekerProfile.RemotePreference.REMOTE,
            location_preferences=["Europe"],
            salary_min=70000,
        )

        intent = profile_scout_intent(profile)
        url = profile_scout_url(profile)

        self.assertIn("remote", intent)
        self.assertIn("Software engineer", intent)
        self.assertIn("Python", intent)
        self.assertIn("Europe", intent)
        self.assertIn("/alerts/?", url)
        self.assertIn("source=profile_complete", url)

    def test_welcome_email_has_attributed_free_scout_entry_point(self):
        html = render_to_string(
            "emails/welcome.html",
            {"user": self.seeker_user, "site_url": "https://remoteimpact.org"},
        )
        text = render_to_string(
            "emails/welcome.txt",
            {"user": self.seeker_user, "site_url": "https://remoteimpact.org"},
        )

        self.assertIn("one free set of your strongest matches", html)
        self.assertIn("/alerts/?source=welcome_email", html)
        self.assertIn("/alerts/?source=welcome_email", text)

    def test_profile_completion_defaults_to_personalized_job_results(self):
        request = RequestFactory().get("/impact-profile/")

        self.assertEqual(
            profile_completion_url(request),
            f"{reverse('jobs:job_list')}?sort=best-match",
        )

    def test_profile_completion_preserves_safe_job_destination_and_adds_matching_sort(self):
        request = RequestFactory().get(
            "/impact-profile/",
            {"next": f"{reverse('jobs:job_list')}?q=python"},
        )

        self.assertEqual(
            profile_completion_url(request),
            f"{reverse('jobs:job_list')}?q=python&sort=best-match",
        )

    def test_profile_completion_rejects_external_destination(self):
        request = RequestFactory().get(
            "/impact-profile/", {"next": "https://example.com/steal-session"}
        )

        self.assertEqual(
            profile_completion_url(request),
            f"{reverse('jobs:job_list')}?sort=best-match",
        )
