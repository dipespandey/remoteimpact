from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from ..models import UserProfile, Organization
from ..services.onboarding_service import OnboardingService

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

    def test_seeker_skipped_onboarding(self):
        """
        Seekers should be redirected to account page even if profile is empty.
        get_redirect_for_state returns None for 'onboarding complete' (or skipped)
        """
        # Ensure profile.headline is empty
        self.assertFalse(self.seeker_user.profile.headline)

        redirect_target = OnboardingService.get_redirect_for_state(self.seeker_user)
        self.assertIsNone(
            redirect_target, "Seekers should skip onboarding even with empty profiles"
        )

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
