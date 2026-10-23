"""Read-only product diagnostics. No replay, raw payloads or new tracking identifiers."""
import re
from collections import Counter, defaultdict
from datetime import timedelta, timezone as datetime_timezone

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db.models import Count, Exists, F, OuterRef, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.crypto import salted_hmac

from jobs.models import ConsumerFunnelEvent as Consumer, EmployerFunnelEvent as Employer, JobAlert

RANGES = (7, 30, 90)
SALT = 'admin-product-analytics-v1'


def external_events(qs):
    qs = qs.filter(Q(user__isnull=True) | Q(user__is_staff=False, user__is_superuser=False))
    return qs.exclude(user_id__in=getattr(settings, 'GROWTH_INTERNAL_USER_IDS', ()))


def label(value):
    """Only bounded machine labels; never echo emails, URLs or free-text metadata."""
    if not isinstance(value, str) or not value:
        return 'Unattributed'
    return value if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:\-]{0,79}', value) else 'Redacted label'


def failure_query(family):
    if family == 'employer':
        return Q(event='checkout_failed')
    return Q(event__in=('initial_email_failed', 'checkout_failed')) | Q(
        event='scout_submit_result', metadata__outcome__in=('invalid', 'error', 'blocked'))


def scope_token(kind, value):
    return signing.dumps({'kind': kind, 'value': value}, salt=SALT)


def read_scope(token):
    if not token or len(token) > 512:
        return None
    try:
        data = signing.loads(token, salt=SALT, max_age=86400)
    except (signing.BadSignature, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get('kind') == 'account' and type(data.get('value')) is int and data['value'] > 0:
        return data
    if data.get('kind') == 'flow' and isinstance(data.get('value'), str) and re.fullmatch(r'[a-f0-9]{32}', data['value']):
        return data
    return None


def pseudonym(kind, value):
    return kind + '-' + salted_hmac(SALT, f'{kind}:{value}').hexdigest()[:12]


def _counts(qs):
    return dict(qs.values_list('event').annotate(n=Count('id')))


def _row(event, family):
    meta = event.metadata if isinstance(event.metadata, dict) else {}
    flow = meta.get('scout_flow_id')
    if isinstance(flow, str) and re.fullmatch(r'[a-f0-9]{32}', flow):
        kind, value = 'flow', flow
    elif event.user_id:
        kind, value = 'account', event.user_id
    else:
        kind, value = None, None
    request = event.event in ('post_job_viewed', 'feature_page_requested')
    trial = family == 'consumer' and event.event == 'payment_completed' and meta.get('trial') is True
    labels = dict((Consumer if family == 'consumer' else Employer).Event.choices)
    return {
        'code': event.event,
        'at': event.created_at, 'family': family,
        'event': 'Trial started (not payment)' if trial else labels.get(event.event, 'Unknown event'),
        'actor': pseudonym(kind, value) if kind else 'Anonymous / unlinked',
        'scope': scope_token(kind, value) if kind else '',
        'account_scope': scope_token('account', event.user_id) if kind == 'flow' and event.user_id else '',
        'object': f'Scout #{event.alert_id}' if family == 'consumer' and event.alert_id else
                  f'Job #{event.job_id}' if family == 'employer' and event.job_id else '—',
        'evidence': 'Unverified request; may be scanner' if request else
                    'Recorded event; not a cash ledger' if event.event == 'payment_completed' else 'Recorded action',
    }


STAGES = (
    ('Explore', {'scout_landed', 'scout_previewed', 'scout_form_viewed', 'scout_signup_started'}),
    ('Account', {'account_created'}),
    ('Scout', {'scout_created'}),
    ('Matches', {'initial_email_sent'}),
    ('Engage', {'scout_job_viewed', 'scout_apply_clicked', 'match_relevant', 'match_not_relevant', 'interest_saved',
                'shortlist_viewed', 'candidate_reviewed', 'employer_opt_in'}),
    ('Checkout', {'upgrade_clicked', 'checkout_started', 'checkout_created', 'job_draft_created'}),
    ('Complete', {'payment_completed'}),
)
EMPLOYER_STAGES = (
    ('Request*', {'post_job_viewed', 'feature_page_requested'}),
    ('Draft', {'job_draft_created'}),
    ('Checkout', {'checkout_started'}),
    ('Complete', {'payment_completed'}),
    ('Review', {'shortlist_viewed', 'candidate_reviewed'}),
)


def _journey_visuals(queries, since, now, family, timeline, scope):
    """Bounded discovery list; selected trails use the separately scoped full query."""
    daily = {}
    for key, qs in queries.items():
        daily[key] = {r['day']: r['n'] for r in qs.annotate(day=TruncDate('created_at', tzinfo=datetime_timezone.utc))
                      .values('day').annotate(n=Count('pk')).order_by('day')}
    dates = [since.date() + timedelta(days=i) for i in range((now.date() - since.date()).days + 1)]
    ceiling = max([v for values in daily.values() for v in values.values()] + [1])
    series = [{'date': day, 'consumer': daily['consumer'].get(day, 0),
               'employer': daily['employer'].get(day, 0),
               'consumer_pct': daily['consumer'].get(day, 0) * 100 / ceiling,
               'employer_pct': daily['employer'].get(day, 0) * 100 / ceiling} for day in dates]
    recent = []
    for key, qs in queries.items():
        if family in ('all', key):
            recent.extend((e, key) for e in qs.order_by('-created_at', '-pk')[:1001])
    recent.sort(key=lambda item: (item[0].created_at, item[0].pk), reverse=True)
    truncated = len(recent) > 1000
    groups = {}
    unlinked = 0
    for event, key in recent[:1000]:
        meta = event.metadata if isinstance(event.metadata, dict) else {}
        flow = meta.get('scout_flow_id')
        if event.user_id:
            kind, value = 'account', event.user_id
        elif key == 'consumer' and isinstance(flow, str) and re.fullmatch(r'[a-f0-9]{32}', flow):
            kind, value = 'flow', flow
        else:
            unlinked += 1
            continue
        identity = (kind, value)
        if identity not in groups:
            groups[identity] = {'actor': pseudonym(kind, value), 'scope': scope_token(kind, value),
                                'user_id': value if kind == 'account' else None,
                                'kind': 'Account' if kind == 'account' else 'Anonymous flow',
                                'last_at': event.created_at, 'count': 0, 'codes': set(), 'families': set(),
                                'selected': scope == {'kind': kind, 'value': value}}
        group = groups[identity]
        group['count'] += 1
        group['codes'].add(event.event)
        group['families'].add(key)
    journeys = list(groups.values())
    for group in journeys:
        stages = EMPLOYER_STAGES if group.pop('families') == {'employer'} else STAGES
        reached = [name for name, codes in stages if codes & group['codes']]
        group['stage'] = reached[-1] if reached else 'Other activity'
        del group['codes']
    displayed = timeline[:100]
    selected_codes = {r['code'] for r in displayed}
    stages = EMPLOYER_STAGES if displayed and {r['family'] for r in displayed} == {'employer'} else STAGES
    steps = [{'label': name, 'seen': bool(codes & selected_codes)} for name, codes in stages]
    if scope and scope['kind'] == 'account':
        for row in displayed:
            # On account detail the useful destination is the related Scout flow,
            # not another link back to this same account.
            row['account_scope'] = ''
    return {'daily': series, 'journeys': journeys[:50],
            'journeys_truncated': truncated or len(journeys) > 50,
            'sample_truncated': truncated, 'list_truncated': len(journeys) > 50,
            'journey_sample': min(len(recent), 1000), 'unlinked_sample': unlinked,
            'steps': steps, 'chronological': sorted(timeline[:100], key=lambda r: r['at'])}


def build_product_analytics(days=30, family='all', failures_only=False, scope=None):
    days = days if days in RANGES else 30
    family = family if family in ('all', 'consumer', 'employer') else 'all'
    now = timezone.now()
    since = now - timedelta(days=days)
    queries = {
        'consumer': external_events(Consumer.objects.filter(created_at__gte=since, created_at__lt=now)),
        'employer': external_events(Employer.objects.filter(created_at__gte=since, created_at__lt=now)),
    }
    counts = {key: _counts(qs) for key, qs in queries.items()}
    employer = counts['employer']

    accounts = get_user_model().objects.filter(date_joined__gte=since, date_joined__lt=now,
                                               is_staff=False, is_superuser=False).exclude(
        pk__in=getattr(settings, 'GROWTH_INTERNAL_USER_IDS', ()))
    account_scouts = JobAlert.objects.filter(user__in=accounts, created_at__gte=F('user__date_joined'),
                                           created_at__lt=now)
    scout_accounts = account_scouts.values('user_id').distinct().count()
    emailed_accounts = queries['consumer'].filter(event='initial_email_sent', alert__in=account_scouts,
                                                  user_id=F('alert__user_id'),
                                                  created_at__gte=F('alert__created_at')).values('user_id').distinct().count()
    total_accounts = accounts.count()
    cohort = [
        {'label': 'New non-staff accounts', 'count': total_accounts},
        {'label': 'Created at least one Scout', 'count': scout_accounts},
        {'label': 'Had an initial match email accepted', 'count': emailed_accounts},
    ]
    for row in cohort:
        row['percent'] = round(row['count'] * 100 / total_accounts, 1) if total_accounts else None

    scouts = JobAlert.objects.filter(created_at__gte=since, created_at__lt=now,
                                     user__is_staff=False, user__is_superuser=False).exclude(
        user_id__in=getattr(settings, 'GROWTH_INTERNAL_USER_IDS', ()))
    related = Consumer.objects.filter(alert_id=OuterRef('pk'), user_id=OuterRef('user_id'),
                                     created_at__gte=OuterRef('created_at'), created_at__lt=now)
    scouts = scouts.annotate(
        has_sent=Exists(related.filter(event='initial_email_sent')),
        has_empty=Exists(related.filter(event='initial_matches_empty')),
        has_failed=Exists(related.filter(event='initial_email_failed')),
    )
    outcomes = scouts.aggregate(
        total=Count('id'), sent=Count('id', filter=Q(has_sent=True)),
        empty=Count('id', filter=Q(has_sent=False, has_empty=True)),
        failed=Count('id', filter=Q(has_sent=False, has_empty=False, has_failed=True)),
        unknown=Count('id', filter=Q(has_sent=False, has_empty=False, has_failed=False)),
    )

    # Aggregate at the DB, cap label cardinality, then explicitly disclose truncation.
    campaigns = defaultdict(Counter)
    truncated = False
    for key, qs in queries.items():
        fields = ('event', 'metadata__scout_source', 'metadata__source',
                  'metadata__campaign_labels__utm_source', 'metadata__campaign_labels__utm_campaign',
                  'metadata__utm_source', 'metadata__trial')
        groups = list(qs.values(*fields).annotate(n=Count('id')).order_by('-n', *fields)[:501])
        truncated |= len(groups) > 500
        for row in groups[:500]:
            source = label(row['metadata__campaign_labels__utm_source'] or row['metadata__scout_source']
                           or row['metadata__utm_source'] or row['metadata__source'])
            campaign = label(row['metadata__campaign_labels__utm_campaign'])
            bucket = campaigns[(key, source, campaign)]
            bucket['events'] += row['n']
            if row['event'] in ('post_job_viewed', 'feature_page_requested'):
                bucket['requests'] += row['n']
            if row['event'] == 'scout_created':
                bucket['scouts'] += row['n']
            if row['event'] == 'payment_completed':
                bucket['trial' if key == 'consumer' and row['metadata__trial'] is True else 'completions'] += row['n']
    campaign_rows = [{'family': key[0], 'source': key[1], 'campaign': key[2], **value}
                     for key, value in campaigns.items()]
    campaign_rows.sort(key=lambda r: (-r['events'], r['family'], r['source'], r['campaign']))

    failures = []
    for key, qs in queries.items():
        names = dict((Consumer if key == 'consumer' else Employer).Event.choices)
        for event, count in _counts(qs.filter(failure_query(key))).items():
            failures.append({'family': key, 'event': names.get(event, 'Unknown'), 'count': count})

    timeline = []
    timeline_total = 0
    for key, qs in queries.items():
        if family not in ('all', key):
            continue
        if failures_only:
            qs = qs.filter(failure_query(key))
        if scope:
            if scope['kind'] == 'account':
                qs = qs.filter(user_id=scope['value'])
            elif key == 'consumer':
                qs = qs.filter(metadata__scout_flow_id=scope['value'])
            else:
                continue
        timeline_total += qs.count()
        for event in qs.order_by('-created_at', '-pk')[:100]:
            timeline.append(_row(event, key))
    timeline.sort(key=lambda row: row['at'], reverse=True)
    visuals = _journey_visuals(queries, since, now, family, timeline, scope)
    outcome_rows = [{'label': title, 'count': outcomes[key], 'color': color,
                     'percent': round(outcomes[key] * 100 / outcomes['total'], 1) if outcomes['total'] else 0}
                    for key, title, color in [('sent', 'Email accepted', 'var(--rec-3)'),
                        ('empty', 'No matches', 'var(--rec-4)'), ('failed', 'Send failed', 'var(--rec-2)'),
                        ('unknown', 'No outcome recorded', 'var(--rec-other)')]]
    campaign_max = max([r['events'] for r in campaign_rows] + [1])
    for row in campaign_rows:
        row['percent'] = row['events'] * 100 / campaign_max
    return {
        **visuals, 'outcome_rows': outcome_rows,
        'days': days, 'ranges': RANGES, 'since': since, 'until': now, 'family': family,
        'failures_only': failures_only, 'cohort': cohort, 'outcomes': outcomes,
        'campaigns': campaign_rows[:30], 'campaign_truncated': truncated or len(campaign_rows) > 30,
        'failures': failures, 'timeline': timeline[:100], 'timeline_total': timeline_total,
        'scope_label': pseudonym(scope['kind'], scope['value']) if scope else '',
        'summary': [
            {'label': 'Recorded events', 'count': sum(sum(v.values()) for v in counts.values())},
            {'label': 'Unverified employer requests', 'count': employer.get('post_job_viewed', 0) + employer.get('feature_page_requested', 0)},
            {'label': 'Failure events', 'count': sum(row['count'] for row in failures)},
            {'label': 'Anonymous consumer events', 'count': queries['consumer'].filter(user__isnull=True).count()},
        ],
    }
