"""Session-bound, expiring Scout flow attribution; never an authentication token."""
import hashlib
import re
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from .funnel import record_consumer_event

KEY = 'scout_flows_v1'
PENDING = 'scout_signup_flow_v1'
MAX_AGE = 24 * 60 * 60
SAMPLES = {'home_hero_example_python', 'home_hero_example_climate', 'home_hero_example_health', 'home_hero_example'}


def _flows(request):
    now = time.time()
    return {key: value for key, value in request.session.get(KEY, {}).items()
            if isinstance(value, dict) and now - value.get('started', 0) < MAX_AGE}


def _save(request, flows):
    request.session[KEY] = dict(sorted(flows.items(), key=lambda pair: pair[1]['started'])[-10:])


def get_flow(request, token=None):
    token = token or request.POST.get('scout_flow') or request.GET.get('scout_flow')
    if not isinstance(token, str) or len(token) != 32:
        return None
    flows = _flows(request)
    flow = flows.get(token)
    if not flow:
        return None
    user = request.user
    if flow.get('owner') and (not user.is_authenticated or flow['owner'] != user.pk):
        return None
    if user.is_authenticated and not flow.get('owner'):
        flow['owner'] = user.pk
        _save(request, flows)
    return {**flow, 'id': token}


def start_flow(request, intent='', source='direct', *, personalized=False, reuse_completed=False):
    source = source if re.fullmatch(r'[a-z][a-z0-9_:\-]{0,79}', source) else 'other'
    digest = hashlib.sha256(intent.encode()).hexdigest()
    flows = _flows(request)
    flow = get_flow(request)
    if not flow:
        for token, value in reversed(list(flows.items())):
            if (value.get('digest') == digest and value.get('source') == source and not value.get('completed')
                    and value.get('owner') in (None, getattr(request.user, 'pk', None))):
                flow = get_flow(request, token)
                if flow:
                    break
    if not flow or (flow.get('completed') and not reuse_completed):
        token = uuid.uuid4().hex
        flow = {'id': token, 'started': time.time(), 'source': source[:80], 'digest': digest,
                'owner': request.user.pk if request.user.is_authenticated else None,
                'sample': source in SAMPLES, 'personalized': False}
    changed = flow['digest'] != digest
    flow.update(digest=digest, personalized=bool(flow.get('personalized') or (personalized and changed)))
    flows[flow['id']] = {k: v for k, v in flow.items() if k != 'id'}
    _save(request, flows)
    return flow, changed


def metadata(flow):
    return {'scout_flow_id': flow['id'], 'scout_source': flow['source'],
            'sample_origin': flow['sample'], 'personalized': flow['personalized']} if flow else {}


def record_stage(request, event, flow, *, extra=None, once=False, alert=None):
    # Analytics failure must never break signup or sending the requested email.
    return record_consumer_event(event, user=request.user, alert=alert,
        metadata={**metadata(flow), **(extra or {})},
        dedupe_key=f"scout-flow:{flow['id']}:{event}" if flow and once else None)


def prepare_signup(request, flow):
    request.session.pop(PENDING, None)
    if flow:
        request.session[PENDING] = {'id': flow['id'], 'at': time.time()}


def link_signup(request, user, sociallogin=None):
    pending = request.session.pop(PENDING, None)
    if not pending or time.time() - pending.get('at', 0) > 1800:
        return
    next_url = (sociallogin.state.get('next', '') if sociallogin is not None
                else request.POST.get('next') or request.GET.get('next') or '')
    parsed = urlsplit(next_url)
    token = parse_qs(parsed.query).get('scout_flow', [''])[0]
    if parsed.scheme or parsed.netloc or parsed.path != '/alerts/' or token != pending['id']:
        return
    flow = get_flow(request, token)
    if not flow or flow.get('owner') not in (None, user.pk):
        return
    flows = _flows(request)
    flows[token]['owner'] = user.pk
    _save(request, flows)
    from jobs.models import ConsumerFunnelEvent
    event = ConsumerFunnelEvent.objects.filter(dedupe_key=f'account-created:{user.pk}', user=user).first()
    if event:
        event.metadata = {**(event.metadata or {}), **metadata(flow)}
        event.save(update_fields=['metadata'])


def complete_flow(request, flow):
    if flow:
        flows = _flows(request)
        if flow['id'] in flows:
            flows[flow['id']]['completed'] = True
            _save(request, flows)
