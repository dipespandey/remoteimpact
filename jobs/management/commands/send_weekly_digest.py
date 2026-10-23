"""Compatibility entrypoint: one consent-aware, deduplicated weekly sender."""
import json
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from jobs.services.roundup import active_subscribers


class Command(BaseCommand):
    help = 'Weekly newsletters use the shared roundup audience and delivery ledger. Preview by default.'

    def add_arguments(self, parser):
        parser.add_argument('--manifest')
        parser.add_argument('--approve-sha256')
        parser.add_argument('--send', action='store_true')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--user')

    def handle(self, *args, **options):
        if options['user']:
            raise CommandError('Recipient overrides are disabled; use the consent-aware roundup audience.')
        if not options['manifest']:
            if options['send']:
                raise CommandError('A reviewed manifest and exact approval hash are required.')
            self.stdout.write(json.dumps({'mode':'preview', 'confirmed_weekly':active_subscribers().count(),
                'requires_completed_profile':False, 'emails_sent':0}))
            return
        call_command('send_roundup', manifest=options['manifest'], approve_sha256=options['approve_sha256'],
                     send=options['send'] and not options['dry_run'], limit=options['limit'], stdout=self.stdout)
