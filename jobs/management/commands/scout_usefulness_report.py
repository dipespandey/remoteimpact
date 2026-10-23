"""Read-only aggregate Scout outcome and explicit relevance report."""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from jobs.services.scout_usefulness import build_scout_usefulness


class Command(BaseCommand):
    help = 'Report retained matches, owner-validated opens/apply clicks and latest feedback. No messages or matching calls.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--since')
        parser.add_argument('--until')
        parser.add_argument('--limit', type=int, default=10000)

    def handle(self, *args, **options):
        if not 1 <= options['days'] <= 366 or not 1 <= options['limit'] <= 50000:
            raise CommandError('days must be 1–366 and limit 1–50000.')
        until = self.timestamp(options['until']) if options['until'] else timezone.now()
        since = self.timestamp(options['since']) if options['since'] else until - timedelta(days=options['days'])
        if since >= until or until > timezone.now() or until - since > timedelta(days=366):
            raise CommandError('Use a positive window of at most 366 days, ending no later than now.')
        self.stdout.write(json.dumps(build_scout_usefulness(since, until, limit=options['limit']), indent=2))

    @staticmethod
    def timestamp(value):
        try:
            parsed = parse_datetime(value)
        except ValueError:
            parsed = None
        if not parsed or timezone.is_naive(parsed):
            raise CommandError('Timestamps must be ISO 8601 with an explicit UTC offset.')
        return parsed
