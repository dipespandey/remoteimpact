import json
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone
from earlycareers.models import DirectoryEvent


class Command(BaseCommand):
    help = 'Report early-careers account handoffs, completed signups, saves and outbound activity.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30)

    def handle(self, *args, **options):
        days = max(1, min(options['days'], 365))
        events = DirectoryEvent.objects.filter(created_at__gte=timezone.now()-timedelta(days=days))
        totals = dict(events.values('event').annotate(total=Count('id')).values_list('event', 'total'))
        self.stdout.write(json.dumps({'days': days, 'events': totals,
            'accounts_created': events.filter(event='signup_complete').values('user_id').distinct().count(),
            'note': 'Visits and handoffs are deduplicated daily. Counts are observational; no conversion lift is established.'}, indent=2))
