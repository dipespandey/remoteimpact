"""Read-only source cohorts using the existing Scout flow events."""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from jobs.models import ConsumerFunnelEvent as E

SOURCES = ('seo_education', 'seo_sustainability', 'seo_nonprofit')


class Command(BaseCommand):
    help = 'Report search-destination Scout flows and observed new-account outcomes; no identifiers or briefs.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30)

    def handle(self, *args, **options):
        now = timezone.now()
        since = now - timedelta(days=max(1, min(90, options['days'])))
        result = {'since_utc': since.isoformat(), 'as_of_utc': now.isoformat(), 'by_destination': {}}
        for source in SOURCES:
            previews = E.objects.filter(event=E.Event.SCOUT_PREVIEWED, created_at__gte=since,
                                       created_at__lt=now, metadata__scout_source=source)
            flows = list(previews.values_list('metadata__scout_flow_id', flat=True).distinct()[:5001])
            flows = [flow for flow in flows if isinstance(flow, str) and len(flow) == 32]
            capped = len(flows) > 5000
            flows = flows[:5000]
            # A reused flow first seen before the window is not a newly acquired flow.
            earlier = set(E.objects.filter(event=E.Event.SCOUT_PREVIEWED, created_at__lt=since,
                metadata__scout_flow_id__in=flows).values_list('metadata__scout_flow_id', flat=True))
            flows = [flow for flow in flows if flow not in earlier]
            events = E.objects.filter(created_at__gte=since, created_at__lt=now, metadata__scout_flow_id__in=flows)
            stages = {name: events.filter(event=event).values('metadata__scout_flow_id').distinct().count()
                      for name, event in [('preview', E.Event.SCOUT_PREVIEWED), ('signup_started', getattr(E.Event, 'SCOUT_SIGNUP_STARTED', 'scout_signup_started')),
                        ('account_created', E.Event.ACCOUNT_CREATED), ('scout_created', E.Event.SCOUT_CREATED)]}
            # Later billing events often lack a flow ID. Report subsequent outcomes
            # only for accounts actually created in the linked cohort, never all users.
            accounts = dict(events.filter(event=E.Event.ACCOUNT_CREATED, user__isnull=False)
                            .order_by('-created_at').values_list('user_id', 'created_at'))
            trials, completions, users_with_completion = 0, 0, set()
            for user, at, metadata in E.objects.filter(event=E.Event.PAYMENT_COMPLETED,
                    user_id__in=accounts, created_at__gte=since, created_at__lt=now).values_list('user_id', 'created_at', 'metadata'):
                if at < accounts[user]:
                    continue
                users_with_completion.add(user)
                if isinstance(metadata, dict) and metadata.get('trial') is True:
                    trials += 1
                else:
                    completions += 1
            result['by_destination'][source.removeprefix('seo_')] = {
                'observed_preview_events': previews.count(), 'linked_new_flows': len(flows),
                'unlinked_preview_events': previews.exclude(metadata__has_key='scout_flow_id').count(),
                'capped': capped, 'stage_flows': stages,
                'new_accounts_with_completion_event': len(users_with_completion),
                'recorded_trial_start_events': trials, 'other_completion_events': completions,
            }
        result['caveats'] = [
            'GA4 events seo_landing_view, seo_job_view, seo_apply_click and seo_scout_click measure the preceding browser journey; this report starts at server-observed Scout previews.',
            'Source labels identify a destination entry point, not proof of Bing acquisition. Use GA4 discovery_source or its acquisition reports to distinguish search engines.',
            'Session loss, cross-device navigation, analytics blocking and expired flows can leave stages unlinked. Historical activity is not backfilled.',
            'Billing outcomes follow linked new accounts, not a causal attribution model; groups may overlap and recent accounts have less observation time.',
            'A trial or completion event is not proof of cash revenue. Paid revenue requires payment receipts; never add these event counts as revenue.',
            'Data can include internal testing. Zero new-flow rows before rollout do not establish zero customer activity.',
        ]
        self.stdout.write(json.dumps(result, indent=2))
