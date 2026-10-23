"""Aggregate retained-match evidence, without equating transport with usefulness."""
import re
from collections import Counter

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from jobs.models import ConsumerFunnelEvent as E, JobAlert, JobAlertDelivery
from jobs.services.product_analytics import label


def build_scout_usefulness(since, until, *, limit=10000):
    scouts = JobAlert.objects.filter(created_at__gte=since, created_at__lt=until,
        user__is_staff=False, user__is_superuser=False).exclude(
        user_id__in=getattr(settings, 'GROWTH_INTERNAL_USER_IDS', ()))
    rows = list(scouts.order_by('created_at', 'pk').values('pk', 'user_id', 'created_at')[:limit])
    by_alert = {row['pk']: row for row in rows}
    sources, flows = {}, {}
    creations = E.objects.filter(event=E.Event.SCOUT_CREATED, alert_id__in=by_alert,
        user_id=F('alert__user_id'), created_at__gte=F('alert__created_at'), created_at__lt=until)
    for alert_id, metadata in creations.order_by('created_at', 'pk').values_list('alert_id', 'metadata'):
        if alert_id in sources:
            continue
        metadata = metadata if isinstance(metadata, dict) else {}
        sources[alert_id] = label(metadata.get('scout_source') or metadata.get('source'))
        flow = metadata.get('scout_flow_id')
        if isinstance(flow, str) and re.fullmatch('[a-f0-9]{32}', flow):
            flows[alert_id] = flow
    preview_flows = set(E.objects.filter(event=E.Event.SCOUT_PREVIEWED,
        metadata__scout_flow_id__in=list(flows.values()), created_at__lt=until)
        .values_list('metadata__scout_flow_id', flat=True))
    deliveries = list(JobAlertDelivery.objects.filter(alert_id__in=by_alert,
        sent_at__gte=F('alert__created_at'), sent_at__lt=until).values(
            'pk', 'alert_id', 'job_id', 'sent_at', 'feedback', 'feedback_at'))
    by_delivery = {row['pk']: row for row in deliveries}
    opened, applied = set(), set()
    legacy_views = 0
    events = E.objects.filter(alert_id__in=by_alert, user_id=F('alert__user_id'),
        created_at__gte=F('alert__created_at'), created_at__lt=until,
        event__in=(E.Event.SCOUT_JOB_VIEWED, E.Event.SCOUT_APPLY_CLICKED))
    for event, alert_id, metadata, at in events.values_list('event', 'alert_id', 'metadata', 'created_at'):
        metadata = metadata if isinstance(metadata, dict) else {}
        delivery_id = metadata.get('delivery_id')
        delivery = by_delivery.get(delivery_id) if type(delivery_id) is int else None
        signal = 'owner_delivery_beacon' if event == E.Event.SCOUT_JOB_VIEWED else 'owner_delivery_apply_redirect'
        verified = (metadata.get('tracking_version') == 1 and metadata.get('signal') == signal
            and delivery and delivery['alert_id'] == alert_id and delivery['job_id'] == metadata.get('job_id')
            and delivery['sent_at'] <= at)
        if verified:
            (opened if event == E.Event.SCOUT_JOB_VIEWED else applied).add(delivery_id)
        elif event == E.Event.SCOUT_JOB_VIEWED:
            legacy_views += 1

    def summarize(alert_ids):
        selected = [row for row in deliveries if row['alert_id'] in alert_ids]
        selected_ids = {row['pk'] for row in selected}
        feedback = Counter()
        for row in selected:
            choice, at = row['feedback'], row['feedback_at']
            if choice in ('relevant', 'not_relevant') and at and row['sent_at'] <= at < until:
                feedback[choice] += 1
            elif choice:
                feedback['unknown_as_of_window_end'] += 1
            else:
                feedback['unrated'] += 1
        rated = feedback['relevant'] + feedback['not_relevant']
        matched_alerts = {row['alert_id'] for row in selected}
        return {'scouts': len(alert_ids), 'accounts': len({by_alert[a]['user_id'] for a in alert_ids}),
            'scouts_with_linked_preview': sum(flows.get(a) in preview_flows for a in alert_ids),
            'scouts_with_retained_matches': len(matched_alerts), 'retained_delivery_records': len(selected),
            'accounts_with_retained_matches': len({by_alert[a]['user_id'] for a in matched_alerts}),
            'distinct_deliveries_opened': len(opened & selected_ids),
            'accounts_with_verified_job_open': len({by_alert[row['alert_id']]['user_id'] for row in selected if row['pk'] in opened}),
            'distinct_deliveries_with_apply_click': len(applied & selected_ids),
            'accounts_with_verified_apply_click': len({by_alert[row['alert_id']]['user_id'] for row in selected if row['pk'] in applied}),
            'latest_explicit_feedback': {key: feedback[key] for key in ('relevant', 'not_relevant', 'unrated', 'unknown_as_of_window_end')},
            'accounts_marking_at_least_one_match_relevant': len({by_alert[row['alert_id']]['user_id'] for row in selected
                if row['feedback'] == 'relevant' and row['feedback_at'] and row['sent_at'] <= row['feedback_at'] < until}),
            'relevant_share_of_rated_matches': round(feedback['relevant'] / rated, 4) if rated else None}

    source_groups = {}
    for alert_id in by_alert:
        source_groups.setdefault(sources.get(alert_id, 'Unattributed'), set()).add(alert_id)
    return {'since_utc': since.isoformat(), 'until_utc': until.isoformat(),
        'feedback_snapshot_read_at_utc': timezone.now().isoformat(),
        'cohort': 'Saved Scouts created in the window, observed through its end. Not a sequential people funnel.',
        'capped': scouts.count() > limit, 'cohort_limit': limit,
        'totals': summarize(set(by_alert)),
        'by_scout_entry_source': {source: summarize(ids) for source, ids in sorted(source_groups.items())},
        'legacy_or_unverified_job_view_events': legacy_views,
        'caveats': [
            'Retained delivery records mean matching transport was accepted; they do not prove inbox delivery, reading or usefulness.',
            'Verified opens are visible-page beacons from signed-in owners of retained matches; bot-shaped agents are excluded, but browser-shaped automation can remain.',
            'Apply clicks are safe outbound redirects after a page-view beacon, not submitted applications or hires.',
            'Only tracking-version-1 owner/delivery-linked engagement is counted; legacy and logged-out activity remain unknown.',
            'Current retained feedback is one latest explicit answer per match. No answer is unknown, not negative.',
            'Feedback changed after the window end cannot reconstruct the earlier answer and is marked unknown.',
            'Known staff/internal accounts are excluded; unknown tests may remain. Deleted Scouts/deliveries are not retained in this cohort.',
            'Scouts and accounts overlap across sources. Recent Scouts have less time to engage. This report makes no model, email or payment-provider calls.',
        ]}
