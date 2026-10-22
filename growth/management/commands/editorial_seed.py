import json
from django.core.management.base import BaseCommand
from growth.seed import OPPORTUNITIES, seed_editorial


class Command(BaseCommand):
    help = "Preview 10 review-needed opportunities and 3 native article drafts; --apply saves drafts only."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        result = seed_editorial() if options["apply"] else {"candidate_keys": [row[0] for row in OPPORTUNITIES]}
        self.stdout.write(json.dumps({"dry_run": not options["apply"], "published": 0, "paid_model_calls": 0, **result}, indent=2))
