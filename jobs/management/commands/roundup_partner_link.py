"""Public, non-personal partner campaign links; no outreach side effects."""
import json
from urllib.parse import urlencode
from django.core.management.base import BaseCommand, CommandError
from jobs.services.roundup import public_url, safe_label, TOPICS

class Command(BaseCommand):
    help = "Create a public partner newsletter signup link. Does not contact anyone."
    def add_arguments(self, parser):
        parser.add_argument("--partner", required=True, help="Public organization/campaign slug, never a person's email")
        parser.add_argument("--topic", choices=list(TOPICS), default="all")
    def handle(self, *args, **options):
        partner = safe_label(options["partner"])
        if not partner or len(partner) > 48:
            raise CommandError("Use a public partner slug of 1–48 lowercase letters, digits, hyphens or underscores.")
        self.stdout.write(json.dumps({"partner": partner, "url": public_url("jobs:roundup") + "?" + urlencode({"utm_source":"partner", "utm_medium":"referral", "utm_campaign":"partner_"+partner, "topic":options["topic"]}), "contacted":False}))
