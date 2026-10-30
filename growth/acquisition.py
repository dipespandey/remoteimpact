"""Observe → prioritise → prepare → measure. No autonomous public communication.

External pages are evidence only. They never grant permission, execute code, or
supply instructions to an LLM. Drafts are deterministic, inspectable proposals.
"""
from collections import Counter
from datetime import timedelta
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
import time
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from jobs.link_models import LinkProspect
from jobs.models import ConsumerFunnelEvent, EmployerFunnelEvent, Job, EmployerOutreach
from jobs.services import link_prospecting
from jobs.services.product_analytics import external_events
from jobs.services.roundup import sample_jobs
from .models import AcquisitionAutomation, AcquisitionCampaign, AcquisitionDaily, AcquisitionRun, CashMovement

BLOCKED_HOSTS = ('reddit.com', 'redd.it')
BLOCKED_PROSPECTS = ('declined', 'do_not_contact')


def campaign_url(campaign, url=None):
    target = url or campaign.destination
    if not link_prospecting.target_is_ours(target):
        return ''
    parsed = urlsplit(target)
    params = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith('utm_')]
    params += [('utm_source', campaign.source), ('utm_medium', 'referral'), ('utm_campaign', 'growth_partnerships')]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), ''))


def restricted_host(url):
    host = (urlsplit(url).hostname or '').lower()
    return any(host == root or host.endswith('.' + root) for root in BLOCKED_HOSTS)


def discover(sector, limit=5):
    from ddgs import DDGS
    query = link_prospecting.QUERIES[sector][0]
    results = DDGS(timeout=10).text(query, max_results=limit)
    count = 0
    for item in list(results)[:limit]:
        url = item.get('href', '')
        try:
            if restricted_host(url) or link_prospecting.target_is_ours(url):
                continue
            _, created = link_prospecting.import_prospect(
                {'url': url, 'name': item.get('title', '')[:255], 'sector': sector},
                source=f'Growth discovery: {query}')
            count += int(created)
        except (ValidationError, ValueError):
            continue
    return count


def partner_draft(campaign, prospect):
    lines = [f'Hello {prospect.name} team,', '',
             'I run RemoteImpact, a job board for remote work in climate, global health, AI safety and social impact.',
             f'I found your resource here: {prospect.url}', '']
    if prospect.fit_reason:
        lines += [prospect.fit_reason, '']
    lines += ['Would a tailored jobs roundup or a resource-page inclusion be useful for your audience?',
              'Here is a catalogue sample for editorial review. Please verify each employer source and applicant eligibility before sharing.', '']
    jobs = sample_jobs(prospect.sector, limit=3)
    for job in jobs:
        lines += [f'{job.title} — {job.organization.name}',
                  f'Location as listed: {job.location or "Not specified"}',
                  campaign_url(campaign, settings.SITE_URL.rstrip('/') + job.get_absolute_url()),
                  f'Employer/application source: {job.application_url}', '']
    if not jobs:
        lines += ['No current catalogue sample was available for this sector.', '']
    lines += [campaign_url(campaign), '',
              'If this is not relevant, let me know and I will not follow up.', 'Dipesh · RemoteImpact']
    return '\n'.join(lines), [{'job_id': job.pk, 'source': job.application_url} for job in jobs]


@transaction.atomic
def persist_proposal(candidate):
    current = AcquisitionCampaign.objects.select_for_update().get(pk=candidate.pk)
    if current.status in ('hold', 'dismissed'):
        return current
    if candidate.prospect_id and LinkProspect.objects.filter(pk=candidate.prospect_id, status__in=BLOCKED_PROSPECTS).exists():
        current.status, current.hold_reason = 'hold', 'Partner declined or opted out.'
        current.save(update_fields=['status', 'hold_reason'])
        return current
    fields = ['title', 'destination', 'score', 'reasons', 'evidence', 'status', 'next_action', 'hold_reason']
    for field in fields:
        setattr(current, field, getattr(candidate, field))
    if not current.draft:
        current.draft, current.draft_created_at = candidate.draft, candidate.draft_created_at
        fields += ['draft', 'draft_created_at']
    current.save(update_fields=fields+['updated_at'])
    return current


def sync_partner(prospect, *, inspect=True):
    campaign, _ = AcquisitionCampaign.objects.get_or_create(key=f'partner:{prospect.pk}', defaults={
        'title': prospect.name, 'audience': 'seeker', 'prospect': prospect,
        'source': f'link_prospect_{prospect.pk}', 'destination': prospect.target_url or link_prospecting.default_target(prospect)})
    if campaign.status in ('dismissed', 'hold'):
        return campaign
    if prospect.status in BLOCKED_PROSPECTS or restricted_host(prospect.url):
        campaign.status = 'hold'
        campaign.hold_reason = 'Existing opt-out/decline or Reddit publishing hold. Review in the original workflow.'
        campaign.next_action = 'Resolve the existing restriction before any outreach.'
        campaign.save()
        return campaign
    stale = not prospect.last_checked_at or prospect.last_checked_at < timezone.now() - timedelta(days=7)
    if inspect and stale:
        link_prospecting.inspect_page(prospect, placement=bool(prospect.placement_url))
    reasons = []
    score = 10
    if prospect.audience and prospect.fit_reason:
        score += 30
        reasons.append('Audience and specific benefit recorded')
    fresh = prospect.last_checked_at and prospect.last_checked_at >= timezone.now() - timedelta(days=7)
    if fresh and prospect.check_status in ('ok', 'target_link_found', 'target_link_not_found'):
        score += 15
        reasons.append('Page inspected within seven days')
    if prospect.contact_source_url and (prospect.contact_url or prospect.contact_email):
        score += 15
        reasons.append('Contact channel has a recorded source')
    if prospect.status == 'replied':
        score += 20
        reasons.append('Partner has replied; review their request')
    if prospect.follow_up_on and prospect.follow_up_on <= timezone.localdate():
        reasons.append('Follow-up date reached; review replies and opt-outs first')
    # A small observed-outcome bonus; never manufacture a probability or forecast.
    previous = campaign.metrics or {}
    if previous.get('scouts', 0) > 0:
        score += 10
        reasons.append('Recorded campaign has produced Scouts')
    campaign.title = prospect.name
    campaign.destination = prospect.target_url or link_prospecting.default_target(prospect)
    campaign.score = min(score, 100)
    campaign.reasons = reasons or ['Audience fit still needs research']
    campaign.evidence = {'source_url': prospect.url, 'checked_at': prospect.last_checked_at.isoformat() if prospect.last_checked_at else None,
                         'check_status': prospect.check_status, 'permission': 'Not inferred from page content', 'method': 'rules-v1'}
    if prospect.status in ('contacted', 'replied', 'placed'):
        campaign.status = 'placed' if prospect.check_status == 'target_link_found' and fresh else 'contacted'
        campaign.next_action = 'Review partner response and placement results in Backlinks.'
    else:
        campaign.status = 'review' if score >= 55 else 'research'
        campaign.next_action = ('Review the sourced contact, audience fit and prepared roundup.' if score >= 55
                                else 'Confirm audience fit and a permitted contact channel.')
        # Preserve existing human drafts. Only create a proposal in this separate workspace.
        if not campaign.draft:
            campaign.draft, jobs = partner_draft(campaign, prospect)
            campaign.draft_created_at = timezone.now()
            campaign.evidence['sample_jobs'] = jobs
    return persist_proposal(campaign)


def sync_employers(limit=10):
    from jobs.services.outreach import outreach_targets
    total = 0
    for row in outreach_targets(days=7, limit=limit):
        org = row['org']
        campaign, _ = AcquisitionCampaign.objects.get_or_create(key=f'employer:{org.pk}', defaults={
            'title': org.name, 'audience': 'employer', 'organization': org,
            'source': f'growth_employer_{org.pk}', 'destination': row['feature_link']})
        if campaign.status in ('hold', 'dismissed'):
            continue
        if row['status'] == EmployerOutreach.Status.NOT_INTERESTED:
            campaign.status, campaign.hold_reason = 'hold', 'Employer marked not interested in outreach.'
            campaign.next_action = 'Do not contact.'
            campaign.save(update_fields=['status', 'hold_reason', 'next_action'])
            continue
        if row['status'] != EmployerOutreach.Status.NEW:
            campaign.status = 'contacted'
            campaign.next_action = 'Review existing employer outreach; do not repeat first contact.'
        else:
            campaign.status = 'review'
            campaign.next_action = 'Verify a business contact and review the existing employer report.'
        campaign.score = min(90, 25 + min(row['clicks'], 20) * 2 + min(row['views'] // 10, 20))
        campaign.reasons = [f"{row['clicks']} application-link clicks and {row['views']} listing views in seven days; not unique applicants",
                            f"{row['live_roles']} available roles"]
        campaign.evidence = {'method': 'rules-v1', 'checked_at': timezone.now().isoformat(), 'report_url': row['report_link']}
        campaign.destination = row['feature_link']
        if not campaign.draft:
            campaign.draft = row['body'].replace(row['feature_link'], campaign_url(campaign))
            campaign.draft_created_at = timezone.now()
        persist_proposal(campaign)
        total += 1
    return total


def metrics_for(campaign, days=30):
    now = timezone.now()
    since = now - timedelta(days=days)
    label_query = (Q(metadata__campaign_labels__utm_source=campaign.source) |
                   Q(metadata__utm_source=campaign.source) | Q(metadata__scout_source=campaign.source))
    consumer = external_events(ConsumerFunnelEvent.objects.filter(created_at__gte=since, created_at__lt=now))
    employer = external_events(EmployerFunnelEvent.objects.filter(created_at__gte=since, created_at__lt=now))
    acquired = consumer.filter(label_query, event='account_created').exclude(user_id=None)
    account_ids = acquired.values('user_id')
    # Later actions of the acquisition cohort supplement directly tagged events.
    cq = consumer.filter(label_query | Q(user_id__in=account_ids))
    counts = dict(cq.values_list('event').annotate(n=Count('id')))
    eq = employer.filter(label_query)
    matured = list(acquired.filter(created_at__lte=now-timedelta(days=7)).values('user_id', 'created_at')[:1001])
    returns = 0
    for signup in matured[:1000]:
        returns += int(consumer.filter(user_id=signup['user_id'], created_at__gte=signup['created_at']+timedelta(days=1),
                                       created_at__lt=signup['created_at']+timedelta(days=8)).exists())
    # Only exact local job-payment references, never event amounts or trials.
    intents = Job.objects.filter(funnel_events__in=eq.filter(event='payment_completed')).exclude(stripe_payment_intent='').values('stripe_payment_intent')
    money = CashMovement.objects.filter(scope_verified=True, livemode=True, payment_intent_id__in=intents,
                                        occurred_at__gte=since, occurred_at__lt=now)
    currencies = list(money.values('currency').annotate(net_minor=Sum('net_minor'), gross_minor=Sum('amount_minor')))
    visits = AcquisitionDaily.objects.filter(campaign=campaign, date__gte=since.date()).aggregate(n=Sum('landing_sessions'))['n']
    return {'since': since.isoformat(), 'until': now.isoformat(), 'landing_sessions': visits,
            'accounts': acquired.values('user_id').distinct().count(), 'scouts': counts.get('scout_created', 0),
            'saves': counts.get('interest_saved', 0), 'apply_clicks': counts.get('scout_apply_clicked', 0),
            'checkouts': eq.filter(event='checkout_started').count(), 'returning_accounts': returns,
            'mature_accounts': min(len(matured), 1000), 'return_coverage_capped': len(matured) > 1000,
            'verified_cash': currencies,
            'cash_coverage': 'Matched reconciled employer job-posting payments only; feature and subscription cash are not attributed here.',
            'coverage': 'Campaign-tagged events plus signup-cohort actions. Session-day landing counts begin at activation, may include scanners, and are not unique people. Returns mean a recorded action on days 1–7, not every site visit. Missing cash reconciliation is not zero revenue.'}


def refresh_metrics(campaign):
    campaign.metrics = metrics_for(campaign)
    campaign.metrics_at = timezone.now()
    campaign.save(update_fields=['metrics', 'metrics_at'])


def record_landing(request, response):
    from jobs.services.job_stats import is_probably_bot
    if request.method != 'GET' or response.status_code != 200 or request.path.startswith(('/admin/', '/accounts/')):
        return
    if getattr(request.user, 'is_staff', False) or is_probably_bot(request):
        return
    source = request.GET.get('utm_source', '')
    if not source.startswith(('link_prospect_', 'growth_employer_')) or len(source) > 100:
        return
    campaign = AcquisitionCampaign.objects.filter(source=source).only('id').first()
    if not campaign:
        return
    today = timezone.localdate()
    seen = request.session.get('growth_landings', {})
    seen = seen if isinstance(seen, dict) else {}
    key = str(campaign.pk)
    if seen.get(key) == today.isoformat():
        return
    record, _ = AcquisitionDaily.objects.get_or_create(campaign=campaign, date=today)
    AcquisitionDaily.objects.filter(pk=record.pk).update(landing_sessions=F('landing_sessions')+1)
    request.session['growth_landings'] = {**dict(list((k, v) for k, v in seen.items() if v == today.isoformat())[-19:]), key: today.isoformat()}


@transaction.atomic
def claim_run(force=False):
    AcquisitionAutomation.objects.get_or_create(pk=1)
    config = AcquisitionAutomation.objects.select_for_update().get(pk=1)
    now = timezone.now()
    if not config.enabled or (config.lease_until and config.lease_until > now):
        return None
    if not force and not config.requested and config.last_started_at and config.last_started_at > now-timedelta(hours=24):
        return None
    if config.lease_token:
        AcquisitionRun.objects.filter(token=config.lease_token, status='running').update(
            status='interrupted', finished_at=now, errors=['Previous worker lease expired; partial observations retained.'])
    token = str(uuid.uuid4())
    config.requested = False
    config.last_started_at = now
    config.lease_until = now+timedelta(minutes=10)
    config.lease_token = token
    config.save()
    return AcquisitionRun.objects.create(token=token), config.discovery_enabled


def run_cycle(force=False):
    claimed = claim_run(force)
    if not claimed:
        return None
    run, discover_enabled = claimed
    started = time.monotonic()
    summary, errors = Counter(), []
    def active():
        return time.monotonic()-started < 180 and AcquisitionAutomation.objects.filter(
            pk=1, enabled=True, lease_token=run.token, lease_until__gt=timezone.now()).exists()
    try:
        AcquisitionCampaign.objects.filter(prospect__status__in=BLOCKED_PROSPECTS).update(
            status='hold', hold_reason='Partner declined or opted out in the source workflow.', next_action='Do not contact.')
        if discover_enabled and active():
            sector = list(link_prospecting.QUERIES)[timezone.localdate().toordinal() % 4]
            try:
                summary['discovered'] = discover(sector)
            except Exception:
                errors.append('Discovery unavailable; existing prospects still processed.')
        prospects = LinkProspect.objects.exclude(status__in=BLOCKED_PROSPECTS).exclude(
            pk__in=AcquisitionCampaign.objects.filter(status__in=['hold', 'dismissed'], prospect_id__isnull=False).values('prospect_id')).order_by(F('last_checked_at').asc(nulls_first=True), 'pk')[:10]
        for prospect in prospects:
            if not active():
                break
            try:
                sync_partner(prospect)
                summary['partners_refreshed'] += 1
            except Exception:
                errors.append(f'Partner #{prospect.pk} could not be refreshed; previous evidence retained.')
        if active():
            summary['employers_refreshed'] = sync_employers()
        for campaign in AcquisitionCampaign.objects.exclude(status__in=['dismissed', 'hold']).order_by(F('metrics_at').asc(nulls_first=True), 'pk')[:50]:
            if not active():
                break
            refresh_metrics(campaign)
            summary['campaigns_measured'] += 1
        run.status = 'partial' if errors or not active() else 'complete'
    except Exception:
        errors.append('Cycle stopped unexpectedly; inspect service logs and retry. No messages were sent.')
        run.status = 'failed'
    finally:
        run.summary = dict(summary)
        run.errors = errors
        run.finished_at = timezone.now()
        run.save()
        AcquisitionAutomation.objects.filter(pk=1, lease_token=run.token).update(lease_until=None, lease_token='')
    return run
