import json
from io import StringIO
from unittest.mock import patch
from django.core.management import call_command
from django.test import TestCase
from growth.models import EmployerPilot
from jobs.models import Organization, OutreachSend


class ProspectQueueTests(TestCase):
    def test_prior_campaign_contact_is_suppressed_even_when_crm_status_is_new(self):
        old = Organization.objects.create(name="Previously contacted", slug="previous-contact")
        fresh = Organization.objects.create(name="Fresh discovery", slug="fresh-discovery")
        OutreachSend.objects.create(organization=old, campaign="prior-campaign", email="private@example.test")
        candidates = [{"org": org, "status": "new", "views": 12, "clicks": 2, "live_roles": 1} for org in (old, fresh)]
        output = StringIO()
        with patch("growth.management.commands.prepare_employer_pilots.outreach_targets", return_value=candidates):
            call_command("prepare_employer_pilots", apply=True, stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(result["sent"], 0)
        self.assertEqual([row["organization_id"] for row in result["prospects"]], [fresh.pk])
        self.assertEqual(EmployerPilot.objects.count(), 1)
        self.assertNotIn("private@example.test", output.getvalue())

    def test_existing_inquiry_and_declined_organization_are_not_recreated(self):
        org = Organization.objects.create(name="Already reviewed", slug="already-reviewed")
        EmployerPilot.objects.create(organization=org)
        candidates = [{"org": org, "status": "new", "views": 12, "clicks": 2, "live_roles": 1}]
        out = StringIO()
        with patch("growth.management.commands.prepare_employer_pilots.outreach_targets", return_value=candidates):
            call_command("prepare_employer_pilots", apply=True, stdout=out)
        self.assertEqual(json.loads(out.getvalue())["prospects"], [])
        self.assertEqual(EmployerPilot.objects.count(), 1)
