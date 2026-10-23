"""Owner-validated engagement with retained Scout matches."""
from django.utils import timezone

from jobs.models import ConsumerFunnelEvent as E, JobAlertDelivery
from jobs.services.funnel import record_consumer_event


def _identifier(value):
    if not value.isascii() or not value.isdigit() or len(value) > 19:
        return None
    parsed = int(value)
    return parsed if 0 < parsed <= 9223372036854775807 else None


def delivery_for_request(request, job_id):
    if not request.user.is_authenticated:
        return None
    deliveries = JobAlertDelivery.objects.select_related('alert').filter(
        alert__user=request.user, job_id=job_id, sent_at__lte=timezone.now())
    reference = request.GET.get('scout_delivery', '')
    if reference:
        identifier = _identifier(reference)
        return deliveries.filter(pk=identifier).first() if identifier else None
    alert_id = _identifier(request.GET.get('alert_id', ''))
    if request.GET.get('source') == 'scout_email' and alert_id:
        return deliveries.filter(alert_id=alert_id).first()
    return None


def record_engagement(request, job_id, event):
    delivery = delivery_for_request(request, job_id)
    if not delivery:
        return
    day = timezone.localdate().isoformat()
    if event == E.Event.SCOUT_APPLY_CLICKED and not E.objects.filter(
        dedupe_key=f'scout-engagement:{E.Event.SCOUT_JOB_VIEWED}:{delivery.pk}:{day}',
        event=E.Event.SCOUT_JOB_VIEWED, user=request.user, alert=delivery.alert,
        metadata__delivery_id=delivery.pk, metadata__job_id=job_id,
        metadata__tracking_version=1, metadata__signal='owner_delivery_beacon',
        created_at__gte=delivery.sent_at).exists():
        return
    return record_consumer_event(event, user=request.user, alert=delivery.alert,
        metadata={'delivery_id': delivery.pk, 'job_id': delivery.job_id,
                  'tracking_version': 1, 'signal': 'owner_delivery_beacon' if event == E.Event.SCOUT_JOB_VIEWED else 'owner_delivery_apply_redirect'},
        dedupe_key=f'scout-engagement:{event}:{delivery.pk}:{day}')
