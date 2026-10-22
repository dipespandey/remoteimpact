import json
from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.utils import timezone

from jobs.models import Category, Job, Organization
from jobs.views.hiring_evidence import HiringEvidenceView, HiringEvidenceSitemap


class HiringEvidenceTests(TestCase):
    def test_sector_scope_and_future_posting_dates(self):
        org = Organization.objects.create(name="Evidence Org", slug="evidence-org")
        climate = Category.objects.create(name="Climate", slug="climate-environment")
        health = Category.objects.create(name="Health", slug="global-health")
        for i, category in enumerate((climate, climate, health)):
            Job.objects.create(title="Researcher", slug=f"evidence-{i}", organization=org,
                               category=category, country="Germany", source="manual",
                               posted_at=timezone.now()+timedelta(days=2) if i == 1 else timezone.now(),
                               experience_level="entry" if i == 0 else "senior",
                               salary_min=50000 if i == 0 else None)
        view = HiringEvidenceView()
        view.request = RequestFactory().get("/insights/climate-hiring/")
        data = view.get_context_data(topic="climate-hiring")
        self.assertEqual(data["counts"]["total"], 2)
        self.assertEqual(data["counts"]["recent"], 1)
        self.assertEqual(data["counts"]["salary"], 1)
        self.assertEqual(data["salary_percent"], 50)
        self.assertEqual(json.loads(data["schema_json"])["@type"], "Dataset")
        early = view.get_context_data(topic="early-career-hiring")
        self.assertEqual(early["counts"]["total"], 1)
        self.assertEqual(len(HiringEvidenceSitemap().items()), 4)
