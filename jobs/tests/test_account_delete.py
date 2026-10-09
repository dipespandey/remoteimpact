from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from jobs.models import (
    JobAlert,
    AssistantGeneration,
    AssistantSubscription,
    EmployerFunnelEvent,
    NewsletterSubscriber,
    Organization,
    UserPath,
    UserProfile,
)


User = get_user_model()


class AccountDeleteViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="delete-me",
            email="delete-me@example.com",
            password="pass123",
        )
        UserProfile.objects.create(
            user=self.user,
            account_type=UserProfile.AccountType.EMPLOYER,
        )
        self.org = Organization.objects.create(
            name="Delete Me Org",
            slug="delete-me-org",
            source=Organization.OrganizationSource.SIGNUP,
        )
        self.org.members.add(self.user)
        JobAlert.objects.create(user=self.user, name="My alert")
        NewsletterSubscriber.objects.create(email=self.user.email)
        UserPath.objects.create(session_key="delete-session", converted_user=self.user)
        AssistantSubscription.objects.create(user=self.user)
        AssistantGeneration.objects.create(
            user=self.user,
            generation_type="cover_letter",
            job_description="A role",
            user_highlights="Skills",
            generated_content="Letter",
        )
        self.funnel_event = EmployerFunnelEvent.objects.create(
            user=self.user, event=EmployerFunnelEvent.Event.POST_JOB_VIEWED
        )
        self.client.force_login(self.user)

    def test_requires_exact_confirmation(self):
        response = self.client.post(
            reverse("jobs:account_delete"),
            {"confirmation": "wrong"},
        )

        self.assertRedirects(response, reverse("jobs:account"))
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=self.org.pk).exists())

    def test_account_page_requires_authentication_before_onboarding_lookup(self):
        self.client.logout()

        response = self.client.get(reverse("jobs:account"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_deletes_user_owned_data_and_single_member_signup_org(self):
        response = self.client.post(
            reverse("jobs:account_delete"),
            {"confirmation": self.user.email},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("jobs:home"))
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(UserProfile.objects.filter(user_id=self.user.pk).exists())
        self.assertFalse(JobAlert.objects.filter(user_id=self.user.pk).exists())
        self.assertFalse(NewsletterSubscriber.objects.filter(email=self.user.email).exists())
        self.assertFalse(UserPath.objects.filter(session_key="delete-session").exists())
        self.assertFalse(Organization.objects.filter(pk=self.org.pk).exists())
        self.assertFalse(AssistantSubscription.objects.filter(user_id=self.user.pk).exists())
        self.assertFalse(AssistantGeneration.objects.filter(user_id=self.user.pk).exists())
        self.funnel_event.refresh_from_db()
        self.assertIsNone(self.funnel_event.user_id)
