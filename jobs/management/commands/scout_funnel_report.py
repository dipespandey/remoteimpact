"""Read-only, preview-anchored Scout cohorts; legacy events stay explicitly unlinked."""
import json
from collections import Counter
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from jobs.models import ConsumerFunnelEvent as E


class Command(BaseCommand):
    help = 'Report linked preview → signup → Scout cohorts without exporting briefs or identifiers.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        now = timezone.now()
        since = now - timedelta(days=max(1, min(90, options['days'])))
        previews = E.objects.filter(event=E.Event.SCOUT_PREVIEWED, created_at__gte=since, created_at__lt=now)
        cohort = {}
        linked = previews.filter(metadata__has_key='scout_flow_id')
        for flow, source, authenticated, sample in linked.order_by('created_at').values_list('metadata__scout_flow_id', 'metadata__scout_source', 'metadata__authenticated', 'metadata__sample_origin')[:5001]:
            if flow and flow not in cohort and len(cohort) < 5000:
                cohort[flow] = {'source': source or 'unknown', 'state': 'authenticated' if authenticated is True else 'anonymous' if authenticated is False else 'unknown', 'sample': bool(sample), 'stages': set(), 'outcomes': Counter()}
        earlier = set(E.objects.filter(event=E.Event.SCOUT_PREVIEWED, created_at__lt=since,
                      metadata__scout_flow_id__in=list(cohort)).values_list('metadata__scout_flow_id', flat=True))
        cohort = {flow: row for flow,row in cohort.items() if flow not in earlier}
        outcomes = E.objects.filter(created_at__gte=since, created_at__lt=now,
                                    metadata__scout_flow_id__in=list(cohort))
        for flow, event, outcome in outcomes.values_list('metadata__scout_flow_id','event','metadata__outcome'):
            cohort[flow]['stages'].add(event)
            if event == E.Event.SCOUT_SUBMIT_RESULT:
                cohort[flow]['outcomes'][outcome or 'unknown'] += 1
        stages = {'preview': E.Event.SCOUT_PREVIEWED, 'personalized': E.Event.SCOUT_PERSONALIZED,
                  'signup_started': E.Event.SCOUT_SIGNUP_STARTED, 'account_created': E.Event.ACCOUNT_CREATED,
                  'confirmation_viewed': E.Event.SCOUT_FORM_VIEWED, 'submit_attempted': E.Event.SCOUT_SUBMIT_STARTED,
                  'scout_created': E.Event.SCOUT_CREATED}
        def summarize(rows):
            return {'flows': len(rows), 'sample_origin_flows': sum(r['sample'] for r in rows), 'stage_flows': {key:sum(event in r['stages'] for r in rows) for key,event in stages.items()},
                    'submit_attempt_outcomes': dict(sum((r['outcomes'] for r in rows),Counter()))}
        sources = sorted({r['source'] for r in cohort.values()})
        preview_ids = E.objects.filter(event=E.Event.SCOUT_PREVIEWED, metadata__has_key='scout_flow_id').values('metadata__scout_flow_id')
        direct_form_flows = E.objects.filter(event=E.Event.SCOUT_FORM_VIEWED, created_at__gte=since, created_at__lt=now,
                        metadata__has_key='scout_flow_id').exclude(metadata__scout_flow_id__in=preview_ids).values('metadata__scout_flow_id').distinct().count()
        result = {'as_of_utc':now.isoformat(),'since_utc':since.isoformat(),
                  'cohort':'Search flows first observed in previews during the window; not people or all new accounts.',
                  'preview_events':previews.count(),'form_flows_without_observed_preview':direct_form_flows,'unlinked_legacy_preview_events':previews.exclude(metadata__has_key='scout_flow_id').count(),
                  'capped': linked.count() > 5000, 'cohort_limit':5000,
                  'totals':summarize(list(cohort.values())),
                  'by_entry_state':{state:summarize([r for r in cohort.values() if r['state']==state]) for state in ['anonymous','authenticated','unknown']},
                  'by_entry_source':{source:summarize([r for r in cohort.values() if r['source']==source]) for source in sources},
                  'caveats':['Outcomes are observed only through report time; recent flows have less time to convert.',
                             'Anonymous session loss, expiration and separate-device signup may remain unlinked.',
                             'Existing authenticated users can skip signup/account-created stages; compare anonymous-entry cohorts separately.',
                             'Legacy events are not retroactively attributed. Counts can include internal/test activity.',
                             'Submit attempts count POSTs received by the server, not clicks blocked by client-side validation.']}
        self.stdout.write(json.dumps(result,indent=2))
