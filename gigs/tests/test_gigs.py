from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jobs.models import Organization, UserProfile
from gigs.models import (
    Gig,
    GigApplication,
    GigCategory,
    RubricCriterion,
    Submission,
    Trial,
)

User = get_user_model()


class GigFlowTests(TestCase):
    def setUp(self):
        self.password = "pass123"
        self.seeker = User.objects.create_user(
            email="seeker@example.com", password=self.password, username="seeker"
        )
        UserProfile.objects.create(
            user=self.seeker, account_type=UserProfile.AccountType.SEEKER, country="US"
        )
        self.employer = User.objects.create_user(
            email="employer@example.com", password=self.password, username="employer"
        )
        UserProfile.objects.create(
            user=self.employer, account_type=UserProfile.AccountType.EMPLOYER
        )
        self.org = Organization.objects.create(name="Org", slug="org")
        self.org.members.add(self.employer)

        self.category = GigCategory.objects.create(
            name="Grant & Fundraising Sprint",
            slug="grant-fundraising",
            description="Test",
        )
        self.gig = Gig.objects.create(
            organization=self.org,
            category=self.category,
            title="Test Gig",
            slug="test-gig",
            remote_policy=Gig.RemotePolicy.ANYWHERE,
            budget_fixed_cents=6000,
            currency="USD",
            trial_fee_cents=5000,
            trial_hours_cap=2,
            trial_due_days=3,
            deliverables=["One brief deliverable"],
            definition_of_done="Definition",
            brief_redacted="Redacted",
            brief_full="Full brief",
            status=Gig.Status.LIVE,
            published_at=timezone.now(),
        )
        RubricCriterion.objects.create(
            gig=self.gig, label="Quality", description="Good", weight=1, max_score=5
        )

    def test_seeker_can_apply_employer_cannot(self):
        apply_url = reverse("gigs:gig_apply", args=[self.gig.slug])

        self.client.force_login(self.seeker)
        resp = self.client.post(
            apply_url, {"motivation": "I can help", "selected_portfolio_items": []}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            GigApplication.objects.filter(seeker=self.seeker, gig=self.gig).count(), 1
        )

        self.client.force_login(self.employer)
        resp = self.client.post(
            apply_url, {"motivation": "I should not", "selected_portfolio_items": []}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            GigApplication.objects.filter(seeker=self.employer, gig=self.gig).count(),
            0,
        )

    def test_employer_can_create_gig_seeker_redirected(self):
        self.client.force_login(self.employer)
        create_url = reverse("gigs:gig_create")
        data = {
            "title": "New Gig",
            "category": self.category.id,
            "remote_policy": Gig.RemotePolicy.ANYWHERE,
            "timezone_overlap": "",
            "budget_fixed_cents": 7500,
            "currency": "USD",
            "trial_fee_cents": 5000,
            "trial_hours_cap": 3,
            "trial_due_days": 4,
            "definition_of_done": "DoD",
            "brief_redacted": "Short brief",
            "brief_full": "Full brief text",
            "deliverables_text": "Deliverable 1",
            "eligible_countries_text": "",
            "rubric_text": "Quality - Meets DoD",
            "nda_required": False,
            "requires_field_verification": False,
            "is_featured": False,
        }
        resp = self.client.post(create_url, data)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Gig.objects.filter(title="New Gig").exists())

        self.client.force_login(self.seeker)
        resp = self.client.get(create_url)
        self.assertEqual(resp.status_code, 302)

    def test_non_owner_cannot_fund_trial(self):
        application = GigApplication.objects.create(
            gig=self.gig, seeker=self.seeker, motivation="Test"
        )
        trial = Trial.objects.create(
            application=application,
            fee_cents=self.gig.trial_fee_cents,
            currency=self.gig.currency,
        )

        other_employer = User.objects.create_user(
            email="other@example.com", password=self.password, username="other"
        )
        UserProfile.objects.create(
            user=other_employer, account_type=UserProfile.AccountType.EMPLOYER
        )
        other_org = Organization.objects.create(name="Other Org", slug="other-org")
        other_org.members.add(other_employer)

        self.client.force_login(other_employer)
        resp = self.client.post(
            reverse("gigs:employer_application", args=[application.id]),
            {"action": "fund", "payment_reference": "ref"},
        )
        self.assertEqual(resp.status_code, 404)
        trial.refresh_from_db()
        self.assertEqual(trial.funding_status, Trial.FundingStatus.NOT_FUNDED)

    def test_submission_blocked_until_funded(self):
        application = GigApplication.objects.create(
            gig=self.gig, seeker=self.seeker, motivation="Test"
        )
        Trial.objects.create(
            application=application,
            fee_cents=self.gig.trial_fee_cents,
            currency=self.gig.currency,
            funding_status=Trial.FundingStatus.NOT_FUNDED,
        )
        self.client.force_login(self.seeker)
        resp = self.client.post(
            reverse("gigs:application_detail", args=[application.id]),
            {
                "artifact_links_text": "http://example.com",
                "notes": "Work",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Submission.objects.filter(application=application).count(), 0)
