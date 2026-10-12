import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from jobs.models import Application, Category, Job, Organization
from jobs.services import hiring_assistant as ha

POSTING = ("We are hiring a program lead. You must have at least five years of experience managing grant-funded "
           "programs, and fluency in Spanish is required. Experience with Salesforce is a plus. " * 3)


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies)

    def generate(self, *args, **kwargs):
        return json.dumps(self.replies.pop(0))


class HiringAssistantTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Org", slug="org")
        cat = Category.objects.create(name="Impact", slug="impact")
        self.job = Job.objects.create(title="Program Lead", slug="program-lead", organization=org, category=cat,
                                      description=POSTING, location="Remote", job_type="full-time",
                                      source=Job.Source.MANUAL, is_paid=True, is_active=True, posted_at=timezone.now())
        user = get_user_model().objects.create_user("ana", "ana@example.com", "pw")
        self.app = Application.objects.create(job=self.job, applicant=user, full_name="Ana",
                                              why_great_fit="I managed grant-funded programs for six years at two NGOs.",
                                              cover_letter="I enjoy building teams.")

    def test_must_haves_need_a_real_quote_from_the_posting(self):
        client = FakeClient({"must_haves": [
            {"label": "Grant program management", "quote": "five years of experience managing grant-funded programs"},
            {"label": "Invented requirement", "quote": "PhD in astrophysics required"},
        ]})
        items = ha.extract_must_haves(self.job, client)
        self.assertEqual([i["label"] for i in items], ["Grant program management"])

    def test_unbacked_met_claims_are_shown_as_not_mentioned(self):
        Job.objects.filter(pk=self.job.pk).update(must_haves=[
            {"label": "Grant programs", "quote": "managing grant-funded programs"},
            {"label": "Spanish", "quote": "fluency in Spanish is required"},
        ])
        self.app.job.refresh_from_db()
        client = FakeClient({"checks": [
            {"index": 0, "status": "met", "quote": "managed grant-funded programs for six years"},
            {"index": 1, "status": "met", "quote": "fluent Spanish speaker"},
        ], "summary": "Six years running grant-funded programs."})
        result = ha.screen_application(self.app, client)
        self.assertEqual([m["status"] for m in result["must_haves"]], ["met", "not_shown"])
        self.assertEqual(result["met"], 1)
        self.assertEqual(result["must_haves"][1]["quote"], "")

    def test_listing_checks_flag_missing_pay_and_location(self):
        tips = ha.listing_checks(self.job)
        self.assertTrue(any("pay range" in t for t in tips))
        self.assertTrue(any("countries" in t for t in tips))
