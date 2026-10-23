"""Preview or reconcile newsletter records without sending email."""
import json
from django.core.management.base import BaseCommand
from jobs.services.newsletter_audience import reconcile_audience


class Command(BaseCommand):
    help = 'Deduplicate newsletter audiences and preserve consent provenance. Preview by default; never sends email.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(reconcile_audience(apply=options['apply']), indent=2))
