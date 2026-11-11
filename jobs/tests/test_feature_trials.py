from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import FeatureTrial, Job, JobDailyStat, JobFeaturePurchase, Organization
from jobs.services import feature_trials
from jobs.tests.test_scout_results import setup_scout_fixture


def make_job(org, title, slug):
    with patch("jobs.services.embedding_service.embed_job", return_value=None):
        return Job.objects.create(title=title, slug=slug, organization=org, location="Remote",
                                  description="A real remote role with responsibilities and outcomes.",
                                  application_url=f"https://example.org/{slug}")


def clicks(job, n, days_ago=1, views=None):
    JobDailyStat.objects.update_or_create(job=job, date=timezone.localdate() - timedelta(days=days_ago),
                                          defaults={"apply_clicks": n, "views": views if views is not None else n * 3})


@override_settings(SECURE_SSL_REDIRECT=False)
class FeatureTrialTests(TestCase):
    def setUp(self):
        setup_scout_fixture(self)
        self.orgs = [Organization.objects.create(name=f"Org {n}", slug=f"org-{n}") for n in range(5)]
        self.top = make_job(self.orgs[0], "Operations Associate", "ops")
        self.second = make_job(self.orgs[0], "Writer", "writer")
        self.paid = make_job(self.orgs[1], "Paid Role", "paid")
        self.tried = make_job(self.orgs[2], "Tried Role", "tried")
        self.small = make_job(self.orgs[3], "Small Role", "small")
        self.next = make_job(self.orgs[4], "Next Role", "next")
        clicks(self.top, 30); clicks(self.second, 5); clicks(self.paid, 40); clicks(self.tried, 35)
        clicks(self.small, 3); clicks(self.next, 12)
        JobFeaturePurchase.objects.create(job=self.paid, stripe_checkout_session_id="cs_1", amount_cents=4900,
                                          featured_until=timezone.now() - timedelta(days=1))
        FeatureTrial.objects.create(job=self.tried, started_at=timezone.now() - timedelta(days=30),
                                    ends_at=timezone.now() - timedelta(days=16))

    def test_trials_go_to_each_top_employers_most_clicked_role_once(self):
        picked = [job for job, _ in feature_trials.candidates(10, min_clicks=10)]
        self.assertEqual(picked, [self.top, self.next])

    def test_apply_features_the_role_and_never_shortens_an_existing_feature(self):
        later = timezone.now() + timedelta(days=20)
        Job.objects.filter(pk=self.next.pk).update(featured_until=later)
        out = StringIO()
        call_command("start_feature_trials", "--apply", "--days", "14", "--min-clicks", "10", stdout=out)
        self.top.refresh_from_db(); self.next.refresh_from_db()
        self.assertTrue(self.top.is_sponsored)
        self.assertEqual(self.next.featured_until, later)
        self.assertEqual(list(FeatureTrial.objects.filter(ends_at__gt=timezone.now()).values_list("job", flat=True)), [self.top.pk])
        self.assertFalse(JobFeaturePurchase.objects.filter(job=self.top).exists())

    def test_dry_run_changes_nothing(self):
        call_command("start_feature_trials", stdout=StringIO())
        self.top.refresh_from_db()
        self.assertFalse(self.top.is_sponsored)
        self.assertEqual(FeatureTrial.objects.count(), 1)

    def test_paying_employers_come_before_trials_in_featured_slots(self):
        now = timezone.now()
        feature_trials.start(self.top, days=3)
        Job.objects.filter(pk=self.paid.pk).update(featured_until=now + timedelta(days=30))
        ordered = list(feature_trials.paid_first(Job.objects.filter(Job.sponsored_filter())))
        self.assertEqual(ordered, [self.paid, self.top])

    def test_only_paid_features_say_by_the_employer(self):
        feature_trials.start(self.top, days=14)
        trial_page = self.client.get(reverse("jobs:job_detail", kwargs={"slug": self.top.slug}))
        self.assertContains(trial_page, "★ Featured")
        self.assertNotContains(trial_page, "by the employer")
        Job.objects.filter(pk=self.paid.pk).update(featured_until=timezone.now() + timedelta(days=30))
        self.assertContains(self.client.get(reverse("jobs:job_detail", kwargs={"slug": self.paid.slug})), "by the employer")

    def test_an_employer_who_buys_during_a_trial_ranks_and_reads_as_paid(self):
        from jobs.services.payment_service import PaymentService
        feature_trials.start(self.top, days=14)
        Job.objects.filter(pk=self.paid.pk).update(featured_until=timezone.now() + timedelta(days=60))
        PaymentService._activate_feature({"id": "cs_trial_buyer", "amount_total": 4900, "metadata": {"job_id": self.top.pk}})
        self.assertFalse(feature_trials.is_trial(self.top))
        self.assertEqual(list(feature_trials.paid_first(Job.objects.filter(Job.sponsored_filter()))), [self.top, self.paid])
        self.assertContains(self.client.get(reverse("jobs:job_detail", kwargs={"slug": self.top.slug})), "by the employer")

    def test_report_compares_trial_days_with_the_days_before(self):
        trial = FeatureTrial.objects.create(job=self.second, started_at=timezone.now() - timedelta(days=4),
                                            ends_at=timezone.now() + timedelta(days=10))
        JobDailyStat.objects.filter(job=self.second).delete()
        clicks(self.second, 2, days_ago=6); clicks(self.second, 9, days_ago=2)
        rows, _ = feature_trials.results([trial])
        self.assertEqual((rows[0]["before"][1], rows[0]["during"][1]), (2, 9))
        out = StringIO()
        call_command("feature_trial_report", stdout=out)
        self.assertIn("apply clicks 2 -> 9 (4.5x)", out.getvalue())
